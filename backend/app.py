from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from passlib.context import CryptContext
import jwt, time, uuid
from typing import Dict, Deque, List, Optional
from collections import defaultdict, deque
from decimal import Decimal, ROUND_DOWN

# --- Config ---
APP_SECRET = "change-me"  # change in real use
SYMBOL = "BTC-USDT"
BASE, QUOTE = "BTC", "USDT"
MAKER_FEE_BPS = Decimal("0")     # 0 for simplicity
TAKER_FEE_BPS = Decimal("10")    # 0.10% taker fee


def D(x):  # Decimal helper
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def q8(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)


# --- Auth / Security ---
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)


def create_jwt(user_id: str, minutes: int = 180) -> str:
    payload = {"sub": user_id, "exp": int(time.time()) + minutes * 60}
    return jwt.encode(payload, APP_SECRET, algorithm="HS256")


def authed(authorization: Optional[str] = Header(default=None, alias="Authorization")):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except Exception:
        raise HTTPException(401, "invalid/expired token")


# --- In-memory state ---

class Bal:
    def __init__(self):
        self.total = D(0)
        self.available = D(0)
        self.locked = D(0)


# users[user_id] = {
#   "pass": "...",
#   "email": str or None,
#   "kyc_status": "pending" | "submitted" | "verified" | "rejected",
#   "is_admin": bool
# }
users: Dict[str, Dict] = {}
wallets: Dict[str, Dict[str, Bal]] = defaultdict(lambda: defaultdict(Bal))
exchange_wallet: Dict[str, Decimal] = defaultdict(Decimal)  # fees accrue here


def _ensure_user_wallets(uid: str):
    _ = wallets[uid][BASE]
    _ = wallets[uid][QUOTE]


# --- Order book / matching ---

class Order:
    def __init__(self, user_id, side, typ, qty, price=None):
        self.id = str(uuid.uuid4())
        self.user_id = user_id
        self.side = side      # "buy" or "sell"
        self.type = typ       # "limit" or "market"
        self.price = q8(D(price)) if price is not None else None
        self.qty = q8(D(qty))
        self.remaining = q8(D(qty))
        self.status = "open"
        self.created_at = int(time.time())


# price -> deque[Order], FIFO within price level
bids: Dict[Decimal, Deque[Order]] = defaultdict(deque)  # buy side
asks: Dict[Decimal, Deque[Order]] = defaultdict(deque)  # sell side
bid_prices: List[Decimal] = []  # sorted desc
ask_prices: List[Decimal] = []  # sorted asc

recent_trades: Deque[Dict] = deque(maxlen=100)


# --- Pydantic models ---

class Register(BaseModel):
    # registration: ONLY user_id + password
    user_id: str
    password: str


class Login(BaseModel):
    user_id: str
    password: str


class KYC(BaseModel):
    # KYC submission: email is required here
    user_id: str
    full_name: Optional[str] = ""
    email: str
    country: Optional[str] = ""
    document_id: Optional[str] = ""


class Deposit(BaseModel):
    user_id: str
    asset: str
    amount: Decimal


class Withdraw(BaseModel):
    user_id: str
    asset: str
    amount: Decimal


class NewOrder(BaseModel):
    user_id: str
    symbol: str = Field(default=SYMBOL)
    side: str    # "buy" | "sell"
    type: str    # "market" | "limit"
    qty: Decimal
    price: Optional[Decimal] = None


# --- FastAPI app setup ---

app = FastAPI(title="Minimal CEX (No DB)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "symbol": SYMBOL}


# --- Auth & KYC ---


@app.post("/auth/register")
def register(d: Register):
    """
    Registration: user_id + password ONLY.
    Email will be added later during KYC submission.
    """
    if d.user_id in users:
        raise HTTPException(400, "user exists")

    is_admin = (d.user_id == "admin")  # convention: 'admin' user is admin
    users[d.user_id] = {
        "pass": hash_password(d.password),
        "email": None,
        "kyc_status": "pending",
        "is_admin": is_admin,
    }
    _ensure_user_wallets(d.user_id)
    return {"ok": True, "is_admin": is_admin}


@app.post("/auth/login")
def login(d: Login):
    u = users.get(d.user_id)
    if not u or not verify_password(d.password, u["pass"]):
        raise HTTPException(401, "bad credentials")
    token = create_jwt(d.user_id)
    return {
        "access_token": token,
        "user_id": d.user_id,
        "is_admin": u.get("is_admin", False),
        "kyc_status": u.get("kyc_status", "pending"),
        "email": u.get("email"),
    }


@app.post("/kyc/submit")
def kyc_submit(d: KYC, user_id: str = Depends(authed)):
    """
    User submits KYC. Email becomes required here.
    """
    if user_id != d.user_id:
        raise HTTPException(403, "submit for yourself only")

    u = users.get(user_id)
    if not u:
        raise HTTPException(404, "user not found")

    if not d.email:
        raise HTTPException(400, "email required for KYC")

    # store email and set status
    u["email"] = d.email
    u["kyc_status"] = "submitted"
    return {"ok": True, "status": u["kyc_status"], "email": u["email"]}


def _require_admin(user_id: str):
    u = users.get(user_id)
    if not u or not u.get("is_admin"):
        raise HTTPException(403, "admin only")


@app.get("/kyc/pending")
def kyc_pending(admin_id: str = Depends(authed)):
    """
    Admin views users with submitted/pending KYC.
    """
    _require_admin(admin_id)
    result = []
    for uid, u in users.items():
        if u.get("kyc_status") in ("submitted", "pending"):
            result.append(
                {
                    "user_id": uid,
                    "email": u.get("email"),
                    "kyc_status": u.get("kyc_status"),
                }
            )
    return result


@app.post("/kyc/admin/verify")
def kyc_admin_verify(
    target_user_id: str,
    status: str = "verified",
    admin_id: str = Depends(authed),
):
    """
    Admin verifies or rejects KYC.
    Email must exist before verifying.
    """
    _require_admin(admin_id)
    u = users.get(target_user_id)
    if not u:
        raise HTTPException(404, "target user not found")

    if status == "verified" and not u.get("email"):
        raise HTTPException(400, "cannot verify without email")

    if status not in ("verified", "rejected", "pending"):
        raise HTTPException(400, "invalid status")

    u["kyc_status"] = status
    return {"ok": True, "user_id": target_user_id, "status": status}


# --- Wallet helpers & endpoints ---


@app.post("/wallet/deposit")
def deposit(d: Deposit, user_id: str = Depends(authed)):
    if user_id != d.user_id:
        raise HTTPException(403, "self only")
    _ensure_user_wallets(user_id)
    w = wallets[user_id][d.asset]
    amt = D(d.amount)
    w.total += amt
    w.available += amt
    return {
        "ok": True,
        "balances": {
            a: {
                "total": str(b.total),
                "available": str(b.available),
                "locked": str(b.locked),
            }
            for a, b in wallets[user_id].items()
        },
    }


@app.post("/wallet/withdraw")
def withdraw(d: Withdraw, user_id: str = Depends(authed)):
    if user_id != d.user_id:
        raise HTTPException(403, "self only")

    u = users.get(user_id)
    if not u or u.get("kyc_status") != "verified":
        raise HTTPException(403, "KYC verification required for withdrawals")

    w = wallets[user_id][d.asset]
    amt = D(d.amount)
    if w.available < amt:
        raise HTTPException(400, "insufficient available")
    fee = q8(amt * D("0.001"))  # 0.1% demo fee
    w.available -= amt
    w.total -= amt
    exchange_wallet[d.asset] += fee
    return {"ok": True, "withdrawn": str(amt - fee), "fee": str(fee)}


@app.get("/wallet/balances/{uid}")
def balances(uid: str, user_id: str = Depends(authed)):
    if user_id != uid:
        raise HTTPException(403, "self only")
    u = users.get(uid, {})
    return {
        "user_id": uid,
        "kyc_status": u.get("kyc_status", "pending"),
        "email": u.get("email"),
        "balances": {
            a: {
                "total": str(b.total),
                "available": str(b.available),
                "locked": str(b.locked),
            }
            for a, b in wallets[uid].items()
        },
    }


# --- Matching helpers ---


def _insert_price(prices: List[Decimal], price: Decimal, reverse: bool):
    if price in prices:
        return
    prices.append(price)
    prices.sort(reverse=reverse)


def _remove_empty_level(
    book: Dict[Decimal, Deque[Order]], prices: List[Decimal], price: Decimal
):
    if price in book and len(book[price]) == 0:
        del book[price]
        prices.remove(price)


def _lock_for_order(
    uid: str, side: str, qty: Decimal, price: Optional[Decimal]
) -> None:
    _ensure_user_wallets(uid)
    if side == "buy":
        if price is None:
            if not ask_prices:
                raise HTTPException(400, "no liquidity")
            est = ask_prices[0] * qty
        else:
            est = price * qty
        b = wallets[uid][QUOTE]
        if b.available < est:
            raise HTTPException(400, "insufficient funds")
        b.available -= est
        b.locked += est
    else:  # sell
        b = wallets[uid][BASE]
        if b.available < qty:
            raise HTTPException(400, "insufficient base")
        b.available -= qty
        b.locked += qty


def _settle_trade(
    maker: Order, taker: Order, px: Decimal, qty: Decimal, taker_is_buy: bool
):
    quote_amt = q8(px * qty)
    taker_fee = q8(quote_amt * TAKER_FEE_BPS / D(10000))
    maker_fee = q8(quote_amt * MAKER_FEE_BPS / D(10000))

    if taker_is_buy:
        # taker buys base, pays quote
        wallets[taker.user_id][BASE].total += qty
        wallets[taker.user_id][BASE].available += qty

        wallets[maker.user_id][BASE].locked -= qty

        wallets[maker.user_id][QUOTE].total += (quote_amt - maker_fee)
        wallets[maker.user_id][QUOTE].available += (quote_amt - maker_fee)

        wallets[taker.user_id][QUOTE].locked -= quote_amt

        exchange_wallet[QUOTE] += (taker_fee + maker_fee)
    else:
        # taker sells base, receives quote
        wallets[taker.user_id][QUOTE].total += (quote_amt - taker_fee)
        wallets[taker.user_id][QUOTE].available += (quote_amt - taker_fee)

        wallets[maker.user_id][QUOTE].locked -= quote_amt

        wallets[maker.user_id][BASE].total += qty
        wallets[maker.user_id][BASE].available += qty

        wallets[taker.user_id][BASE].locked -= qty

        exchange_wallet[QUOTE] += (taker_fee + maker_fee)

    recent_trades.appendleft(
        {
            "symbol": SYMBOL,
            "price": str(px),
            "qty": str(qty),
            "maker_order_id": maker.id,
            "taker_order_id": taker.id,
            "taker_side": "buy" if taker_is_buy else "sell",
            "ts": int(time.time()),
        }
    )


def _match(order: Order):
    # very simple FIFO per price level, price-time priority
    if order.side == "buy":
        # cross against asks from lowest price
        while order.remaining > 0 and ask_prices:
            best = ask_prices[0]
            if order.type == "limit" and order.price < best:
                break
            level = asks[best]
            while level and order.remaining > 0:
                maker = level[0]
                fill = min(order.remaining, maker.remaining)
                _settle_trade(maker, order, best, fill, taker_is_buy=True)
                order.remaining = q8(order.remaining - fill)
                maker.remaining = q8(maker.remaining - fill)
                if maker.remaining == 0:
                    maker.status = "filled"
                    level.popleft()
                if order.remaining == 0:
                    order.status = "filled"
                    break
            if not level:
                _remove_empty_level(asks, ask_prices, best)

        # If limit with remaining -> rest on book, else (market) refunds are approximate
        if order.type == "limit" and order.remaining > 0:
            price = order.price
            bids[price].append(order)
            _insert_price(bid_prices, price, reverse=True)
        else:
            # naive refund for leftover locked quote (for market or fully matched)
            # (for demo only; not production-quality)
            pass

    else:  # sell
        while order.remaining > 0 and bid_prices:
            best = bid_prices[0]
            if order.type == "limit" and order.price > best:
                break
            level = bids[best]
            while level and order.remaining > 0:
                maker = level[0]
                fill = min(order.remaining, maker.remaining)
                _settle_trade(maker, order, best, fill, taker_is_buy=False)
                order.remaining = q8(order.remaining - fill)
                maker.remaining = q8(maker.remaining - fill)
                if maker.remaining == 0:
                    maker.status = "filled"
                    level.popleft()
                if order.remaining == 0:
                    order.status = "filled"
                    break
            if not level:
                _remove_empty_level(bids, bid_prices, best)

        if order.type == "limit" and order.remaining > 0:
            price = order.price
            asks[price].append(order)
            _insert_price(ask_prices, price, reverse=False)


# --- Order endpoints ---


@app.post("/orders")
def place_order(d: NewOrder, user_id: str = Depends(authed)):
    if d.symbol != SYMBOL:
        raise HTTPException(400, "only BTC-USDT supported in this build")
    if d.side not in ("buy", "sell"):
        raise HTTPException(400, "side must be buy or sell")
    if d.type not in ("market", "limit"):
        raise HTTPException(400, "type must be market or limit")

    qty = q8(D(d.qty))
    price = q8(D(d.price)) if d.price is not None else None

    if qty <= 0:
        raise HTTPException(400, "qty must be positive")
    if d.type == "limit" and (price is None or price <= 0):
        raise HTTPException(400, "price must be positive for limit")

    _ensure_user_wallets(user_id)
    _lock_for_order(user_id, d.side, qty, price)

    order = Order(user_id, d.side, d.type, qty, price)
    _match(order)

    return {
        "order_id": order.id,
        "status": order.status,
        "remaining": str(order.remaining),
    }


@app.get("/orders/open/{uid}")
def open_orders(uid: str, user_id: str = Depends(authed)):
    if user_id != uid:
        raise HTTPException(403, "self only")
    out = []
    for price in bid_prices:
        for o in bids[price]:
            if o.user_id == uid and o.remaining > 0:
                out.append(
                    {
                        "id": o.id,
                        "side": o.side,
                        "type": o.type,
                        "price": str(o.price),
                        "remaining": str(o.remaining),
                    }
                )
    for price in ask_prices:
        for o in asks[price]:
            if o.user_id == uid and o.remaining > 0:
                out.append(
                    {
                        "id": o.id,
                        "side": o.side,
                        "type": o.type,
                        "price": str(o.price),
                        "remaining": str(o.remaining),
                    }
                )
    return out


@app.post("/orders/{order_id}/cancel")
def cancel(order_id: str, user_id: str = Depends(authed)):
    # find order on bid side
    for price in list(bid_prices):
        level = bids[price]
        for o in list(level):
            if o.id == order_id and o.user_id == user_id:
                # refund remaining locked quote
                refund = q8(o.price * o.remaining)
                wallets[user_id][QUOTE].locked -= refund
                wallets[user_id][QUOTE].available += refund
                level.remove(o)
                _remove_empty_level(bids, bid_prices, price)
                o.status = "canceled"
                return {"ok": True}
    # ask side
    for price in list(ask_prices):
        level = asks[price]
        for o in list(level):
            if o.id == order_id and o.user_id == user_id:
                refund = o.remaining
                wallets[user_id][BASE].locked -= refund
                wallets[user_id][BASE].available += refund
                level.remove(o)
                _remove_empty_level(asks, ask_prices, price)
                o.status = "canceled"
                return {"ok": True}
    raise HTTPException(404, "order not found")


# --- Market data ---


@app.get("/orderbook/BTC-USDT")
def orderbook():
    def agg(levels: Dict[Decimal, Deque[Order]], is_bids: bool):
        prices = sorted(levels.keys(), reverse=is_bids)
        out = []
        for p in prices:
            qty = sum(o.remaining for o in levels[p])
            if qty > 0:
                out.append([str(p), str(qty)])
        return out[:10]

    return {
        "symbol": SYMBOL,
        "bids": agg(bids, True),
        "asks": agg(asks, False),
    }


@app.get("/trades/BTC-USDT")
def trades():
    return list(recent_trades)[:50]
