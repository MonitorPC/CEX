# Minimal CEX (No DB, No Stress Test)

## 1.

This project is a **minimal centralized cryptocurrency exchange**.

It focuses on **architecture & concepts**:

- **One trading pair**: `BTC-USDT`
- **In-memory state only** (no database)
- **Simple FIFO matching engine**
- **Market & Limit orders**
- **Wallet with available / locked / total**
- **KYC-gated withdrawals**
- **Admin user to verify KYC**
- **Simple multi-page HTML/JS frontend**
- **FastAPI backend (REST API)**

---

## 2. Features

### 2.1 Trading Engine

* Single symbol: **`BTC-USDT`**.
* Order types:

  * **Limit** (`type = "limit"`): placed at a specific price.
  * **Market** (`type = "market"`): executes immediately against the best prices in the book.
* Matching algorithm:

  * **Price-time priority (FIFO per price level)**:

    * Best price first.
    * Within the same price, older orders first.
* Order status:

  * `open`, `filled`, `canceled`.
* Cancellations:

  * User can cancel **their own open orders**, locked funds are released back to **available**.

### 2.2 Wallet System

For each user and asset (`BTC`, `USDT`), we track:

* `total` – total balance the user owns.
* `available` – free to use for new orders / withdrawals.
* `locked` – reserved inside open orders.

Operations:

* **Deposit**: increases `total` and `available`.
* **Withdraw**:

  * Allowed **only if KYC is verified**.
  * Checks `available` >= amount.
  * Deducts from `total` and `available`.
  * Charges a small demo fee (or you can set fee = 0 in code).

Everything is stored **in memory** (Python dictionaries), so restarting the backend resets all state.

### 2.3 KYC & Users

* **Registration**:

  * **Only `user_id` + `password`** are required.
  * Email is *not* required at registration.

* **KYC submission**:

  * Separate page (`kyc.html`).
  * User provides **email** and basic info (`full_name`, `country`, `document_id`).
  * Email is **required** at this step.
  * KYC status flow:

    * `pending` → (user submits KYC) → `submitted` → (admin verifies) → `verified` (or `rejected`)

* **KYC gating**:

  * Deposits and trading work even if not verified.
  * **Withdrawals require `kyc_status == "verified"`**.

### 2.4 Admin

* Special user: `user_id = "admin"` is treated as **admin**.
* Admin functions:

  * View list of users with `pending` / `submitted` KYC.
  * Set each user’s KYC status to:

    * `verified`
    * `rejected`
    * `pending` (if resetting)

Admin UI: `admin.html`.

### 2.5 User Interface (Frontend)

All frontend is plain HTML + JavaScript (no frameworks) for clarity.

* `index.html`

  * Set API URL (default `http://localhost:8000`).
  * Register new user (`user_id`, `password`).
  * Login and store JWT in `localStorage`.
  * Shows links to KYC/Trade/Admin pages after login.

* `kyc.html`

  * Requires login.
  * Shows current user.
  * Lets user submit KYC data (email required).

* `trade.html`

  * Requires login.
  * Shows balances (with `total`, `available`, `locked`).
  * Deposit / Withdraw buttons.
  * Order entry:

    * Side: buy / sell
    * Type: market / limit
    * Quantity (BTC)
    * Price (for limit)
  * Shows:

    * Open orders (with cancel buttons).
    * Order book (top bids & asks).
    * Recent trades.

* `admin.html`

  * Requires login as **admin**.
  * Lists users with pending/submitted KYC.
  * Buttons to **Verify** or **Reject**.

### 2.6 API Gateway

The REST API (FastAPI) acts as an API gateway for the frontend and potential trading bots:

* Authentication & JWT handling.
* KYC submission and admin management.
* Wallet operations (deposit, withdraw, balances).
* Order operations (place, cancel, open orders).
* Market data (order book, recent trades).

---

## 3. Backend – How to Run

### 3.1 Install Python3.12 dependencies

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3.2 Run the API server

```bash
uvicorn app:app --reload
```

* Default address: `http://127.0.0.1:8000`
* Check health:

```bash
curl http://127.0.0.1:8000/health
# {"ok": true, "symbol": "BTC-USDT"}
```

---

## 4. Frontend – How to Run

Simplest: serve the `frontend` folder with Python’s built-in web server.

```bash
cd frontend
python -m http.server 5173
```

Open in browser:

* `http://localhost:5173/index.html`

Set the API URL to `http://127.0.0.1:8000` (or your backend host/port) in the input on top of the page, click **Save**, and you’re ready.

---

## 5. Deployed CEX (Web and API)

You can access the CEX by this link `http://158.160.91.57:5173`

* The API is `http://158.160.91.57:8000`

---

## 6. Typical Demo Flow

### Step 1 – Create admin and normal users

1. Open `index.html`.
2. Register user `admin` (this automatically becomes admin in the backend).
3. Register user `alice` (trader), `bob` (trader).

### Step 2 – Login as Alice and submit KYC

1. Login as `alice` on `index.html`.
2. Click the link to go to **KYC page** (`kyc.html`).
3. Fill in:

   * email (required)
   * full name
   * country
   * document ID
4. Submit – status becomes `submitted`.

### Step 3 – Login as Admin and verify KYC

1. Login as `admin` on `index.html`.
2. Open `admin.html`.
3. You should see `alice` in the list.
4. Click **Verify** to set `kyc_status = "verified"`.

### Step 4 – Deposit funds and trade

1. Login as `alice`.
2. Go to `trade.html`.
3. Deposit some **USDT** (e.g. `10000`).
4. Login as `bob` in another browser/profile, deposit some **BTC** (e.g. `1`).
5. Place a **SELL limit** order as `bob`:

   * side: `sell`
   * type: `limit`
   * qty: `0.1`
   * price: `100`
6. Place a **BUY market** order as `alice`:

   * side: `buy`
   * type: `market`
   * qty: `0.1`
7. Check:

   * order book
   * recent trades
   * balances (BTC & USDT for each user)

### Step 5 – Withdraw (KYC-gated)

1. With `alice` verified:

   * In `trade.html`, withdraw some USDT – it should succeed.
2. For a user without KYC verified:

   * Withdraw endpoint should return an error: KYC required.

---

## 7. API Overview

**Auth**

* `POST /auth/register`

  * Body: `{ "user_id": "...", "password": "..." }`
* `POST /auth/login`

  * Body: `{ "user_id": "...", "password": "..." }`
  * Returns: `{ access_token, user_id, is_admin, kyc_status, email }`

**KYC**

* `POST /kyc/submit`

  * Auth: Bearer token
  * Body: `{ user_id, full_name, email, country, document_id }`
* `GET /kyc/pending`

  * Auth: Bearer token (admin only)
  * Returns list of users with `pending` / `submitted`
* `POST /kyc/admin/verify`

  * Auth: Bearer token (admin only)
  * Query params: `target_user_id`, `status` (`verified` / `rejected` / `pending`)

**Wallet**

* `POST /wallet/deposit`

  * Auth
  * Body: `{ user_id, asset, amount }`
* `POST /wallet/withdraw`

  * Auth, requires `kyc_status == "verified"`
  * Body: `{ user_id, asset, amount }`
* `GET /wallet/balances/{user_id}`

  * Auth, same user only

**Orders & Market Data**

* `POST /orders`

  * Auth
  * Body:

    ```json
    {
      "user_id": "alice",
      "symbol": "BTC-USDT",
      "side": "buy",
      "type": "limit",
      "qty": 0.1,
      "price": 100.0
    }
    ```
* `POST /orders/{order_id}/cancel`

  * Auth, cancels your own open order
* `GET /orders/open/{user_id}`

  * Auth, same user
* `GET /orderbook/BTC-USDT`

  * Returns arrays of bids/asks
* `GET /trades/BTC-USDT`

  * Returns recent trades
