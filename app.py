import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
import mysql.connector
from mysql.connector import errorcode, pooling

load_dotenv()

CATEGORIES = [
    "Salary", "Freelance", "Food", "Rent", "Transport",
    "Shopping", "Bills", "Health", "Other",
]

INCOME_CATEGORIES = ["Salary", "Freelance", "Other"]
EXPENSE_CATEGORIES = ["Food", "Rent", "Transport", "Shopping", "Bills", "Health", "Other"]

# App

app = Flask(__name__)

secret = os.environ.get("SECRET_KEY")
if not secret:
    raise RuntimeError("SECRET_KEY is not set. Copy .env.example to .env first.")
app.secret_key = secret

db_pool = pooling.MySQLConnectionPool(
    pool_name="project1",
    pool_size=5,
    host=os.environ.get("DB_host", "localhost"),
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    database=os.environ.get("DB_NAME","project1"),
)

def get_db():
    """One connection per request, returned to the pool when the request ends."""
    if "db" not in g:
        g.db = db_pool.get_connection()
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db",None)
    if db is not None:
        db.close()

def query(sql, params=(), *, one=False):
    cursor = get_db().cursor(dictionary=True)
    cursor.execute(sql, params)
    result = cursor.fetchone() if one else cursor.fetchall()
    cursor.close()
    return result

def execute(sql, params=()):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    conn.commit()
    cursor.close()
    return cursor.lastrowid

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def current_period(period, anchor_date, today):
    """Return (start, end) dates of the budget period that 'today' falls in."""
    if period == "weekly":
        weeks_elapsed = (today - anchor_date).days // 7
        start = anchor_date + timedelta(days=weeks_elapsed * 7)
        end = start + timedelta(days=6)

    else:
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year, 12, 31)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    return start, end

def get_budget_status(user_id):
    """Returns None if no budget is set, otherwise a dict the dashboard can render."""
    budget = query(
        "SELECT period, anchor_date, total_amount FROM budgets WHERE user_id = %s",
        (user_id,),
        one=True,
    )
    if not budget:
        return None

    start, end = current_period(budget["period"], budget["anchor_date"], date.today())

    spent_row = query(
        """
        SELECT COALESCE(SUM(amount), 0) AS spent
        FROM transactions
        WHERE user_id = %s AND type = 'expense' AND txn_date BETWEEN %s AND %s
        """,
        (user_id, start, end),
        one=True,
    )
    spent = spent_row["spent"]
    total = budget["total_amount"]
    percent = float(spent / total * 100) if total > 0 else 0

    category_limits = query(
        "SELECT category, limit_amount FROM budget_categories WHERE user_id = %s ORDER BY category",
        (user_id,),
    )
    category_spent = {
        row["category"]: row["spent"]
        for row in query(
            """
            SELECT category, COALESCE(SUM(amount), 0) AS spent
            FROM transactions
            WHERE user_id = %s AND type = 'expense' AND txn_date BETWEEN %s AND %s
            GROUP BY category
            """,
            (user_id, start, end),
        )
    }

    categories = []
    for row in category_limits:
        cat_spent = category_spent.get(row["category"], Decimal("0"))
        cat_limit = row["limit_amount"]
        cat_percent = float(cat_spent / cat_limit * 100) if cat_limit > 0 else 0
        categories.append({
            "category": row["category"],
            "spent": cat_spent,
            "limit": cat_limit,
            "percent": min(cat_percent, 100),
            "over": cat_spent > cat_limit,
            "warning": cat_percent >= 90,
        })

    return {
        "period": budget["period"],
        "start": start,
        "end": end,
        "spent": spent,
        "total": total,
        "percent": min(percent, 100),
        "over": spent >total,
        "warning": percent >= 90,
        "categories": categories,
    }


@app.context_processor
def inject_user():
    return{"current_user_name": session.get("user_name")}

# Authentication

@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Fill in your credentials.")
            return render_template("register.html", name=name, email=email), 400

        if len(password) < 8:
            flash("Use a password of a least 8 characters.")
            return render_template("register.html", name=name, email=email), 400

        try:
            execute(
                "INSERT INTO users (name, email, pass_word) VALUES (%s, %s, %s)",
                (name, email, generate_password_hash(password)),
            )

        except mysql.connector.Error as err:
            if err.error == errorcode.ER_DUP_ENTRY:
                flash("That email already has an account. Log in instead or try new email.")
                return render_template("register.html", name=name, email=email), 409
            raise

        flash("Account created. Log in to continue.")
        return redirect(url_for("login"))

    return render_template("register.html", name="", email="")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = query("SELECT * FROM users WHERE email = %s", (email,), one=True)

        if user and check_password_hash(user["pass_word"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            return redirect(url_for("dashboard"))


        flash("Email or Password is incorrect.")
        return render_template("login.html", email=email), 401

    return render_template("login.html", email="")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))

# Money

@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]

    user = query(
        "SELECT opening_balance FROM users WHERE id = %s", (user_id,), one=True
    )
    opening_balance = user["opening_balance"]

    totals = query(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expenses
        FROM transactions
        WHERE user_id = %s
        """,
        (user_id,),
        one=True,
    )

    transactions = query(
        """
        SELECT id, type, amount, category, note, txn_date
        FROM transactions
        WHERE user_id = %s
        ORDER BY  txn_date DESC, id DESC
        LIMIT 25
        """,
        (user_id,),
    )

    balance = opening_balance + totals["income"] - totals["expenses"]

    budget_status = get_budget_status(user_id)

    return render_template(
        "dashboard.html",
        opening_balance=opening_balance,
        total_income=totals["income"],
        total_expenses=totals["expenses"],
        balance=balance,
        transactions=transactions,
        categories=CATEGORIES,
        today=date.today().isoformat(),
        budget=budget_status,
        income_categories=INCOME_CATEGORIES,
        expense_categories=EXPENSE_CATEGORIES,
    )

@app.route("/budget")
@login_required
def budget_page():
    return render_template(
        "budget.html",
        budget=get_budget_status(session["user_id"]),
        expense_categories=EXPENSE_CATEGORIES,
    )

@app.route("/budget/set", methods=["POST"])
@login_required
def set_budget():
    period = request.form.get("period", "")
    raw_amount = request.form.get("total_amount", "")

    if period not in ("weekly", "monthly"):
        flash("Pick weekly or monthly.")
        return redirect(url_for("budget_page"))
    
    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        flash("Enter the budget amount as a number.")
        return redirect(url_for("budget_page"))

    if amount <= 0:
        flash("The budget has to be greater than 0.")
        return redirect(url_for("budget_page"))

    existing = query(
        "SELECT period, anchor_date, total_amount FROM budgets WHERE user_id = %s",
        (session["user_id"],),
        one=True,
    )
    if existing:
        old_start, old_end = current_period(
            existing["period"], existing["anchor_date"], date.today()
        )
        execute(
            """
            INSERT INTO budget_history (user_id, period, start_date, end_date, total_amount)
            VALUES (%s, %s, %s, %s, %s)""",
            (session["user_id"], existing["period"], old_start, old_end, existing["total_amount"]),
        )

    execute(
        """
        INSERT INTO budgets (user_id, period, anchor_date, total_amount)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE period = %s, anchor_date = %s, total_amount = %s
        """,
        (session["user_id"], period, date.today(), amount, period, date.today(), amount),
    )
    flash("Budget saved.")
    return redirect(url_for("budget_page"))

@app.route("/budget/delete", methods=["POST"])
@login_required
def delete_budget():

    execute("DELETE FROM budgets WHERE user_id = %s", (session["user_id"],))
    flash("Budget removed.")
    return redirect(url_for("budget_page"))

@app.route("/budget/categories/set", methods=["POST"])
@login_required
def set_budget_category():
    category = request.form.get("category", "")
    raw_amount = request.form.get("limit_amount", "")

    if category not in EXPENSE_CATEGORIES:
        flash("Pick a valid category.")
        return redirect(url_for ("budget_page"))

    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        flash("Enter the category limit as a number.")
        return redirect(url_for("budget_page"))

    if amount <= 0:
        flash("the category limit has to be greater than 0.")
        return redirect(url_for("budget_page"))

    execute(
        """
        INSERT INTO budget_categories (user_id, category, limit_amount)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE limit_amount = %s
        """,
        (session["user_id"], category, amount, amount),
    )
    flash(f"limit set for {category}.")
    return redirect(url_for("budget_page"))

@app.route("/budget/category/<category>/delete", methods=["POST"])
@login_required
def delete_budget_category(category):
    execute(
        "DELETE FROM budget_categories WHERE user_id = %s AND category = %s",
        (session["user_id"], category),
    )
    return redirect(url_for("budget_page"))

@app.route("/account/opening_balance", methods=["POST"])
@login_required
def set_opening_balance():
    raw_amount = request.form.get("opening_balance", "")

    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        flash("Enter the starting balance as a number such as a number, like 5000 or -250. ")
        return redirect(url_for("dashboard"))

    execute(
        "UPDATE users SET opening_balance = %s WHERE id = %s",
        (amount, session["user_id"]),
    )
    flash("starting balance updated.")
    return redirect (url_for("dashboard"))

@app.route("/transactions/reset", methods=["POST"])
@login_required
def reset_transactions():

    if request.form.get("confirm", "").strip().upper() != "RESET":
        flash('Type RESET exactly to confirm - nothing was deleted.')
        return redirect (url_for("dashbord"))

    execute("DELETE FROM transactions WHERE user_id = %s", (session["user_id"],))
    flash("All transactions deleted. Starting balance was left as is.")
    return redirect(url_for("dashboard"))

@app.route("/transactions/add", methods=["POST"])
@login_required
def add_transaction():
    txn_type = request.form.get("type", "")
    raw_amount = request.form.get("amount", "")
    category = request.form.get("category", "Other")
    note = request.form.get("note", "").strip() or None
    txn_date = request.form.get("txn_date") or date.today().isoformat()

    if txn_type not in ("income", "expense"):
        flash("Pick either income or expense.")
        return redirect(url_for("dashboard"))

    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        flash("Enter the numerical amount, like 123.00 .")
        return redirect(url_for("dashboard"))

    if amount <= 0:
        flash("The amount has to be greater than 0")
        return redirect(url_for("dashboard"))

    if category not in CATEGORIES:
        category = "Other"

    execute(
        """
        INSERT INTO transactions (user_id, type, amount, category, note, txn_date)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (session["user_id"], txn_type, amount, category, note, txn_date),
    )
    return redirect(url_for("dashboard"))

@app.route("/transactions/<int:txn_id>/delete", methods=["POST"])
@login_required
def delete_transaction(txn_id):

    execute(
        "DELETE FROM transactions WHERE id = %s AND user_id = %s",
        (txn_id, session["user_id"]),
    )
    return redirect(url_for("dashboard"))

@app.route("/statistics")
@login_required
def statistics():
    user_id = session["user_id"]
    today = date.today()

    months = []
    cursor_month = today.replace(day=1)
    for _ in range(6):
        months.append(cursor_month)
        cursor_month = (cursor_month - timedelta(days=1)).replace(day=1)
    months.reverse()

    monthly_rows = query(
        """
        SELECT
            DATE_FORMAT(txn_date, '%%Y-%%m') AS ym,
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount END), 0) AS expense
        FROM transactions
        WHERE user_id = %s AND txn_date >= %s
        GROUP BY ym
        """,
        (user_id, months[0]),
    )
    by_month = {row["ym"]: row for row in monthly_rows}

    monthly_summary = []
    for m in months:
        key = m.strftime("%Y-%m")
        row = by_month.get(key, {"income": Decimal("0"), "expenses": Decimal("0")})
        monthly_summary.append({
            "label": m.strftime("%b, %Y"),
            "income": row["income"],
            "expenses": row["expenses"],
            "net": row["income"] - row["expenses"],
        })

    this_month_start = today.replace(day = 1)
    category_rows = query(
        """
        SELECT category, COALESCE(SUM(amount), 0) AS spent
        FROM transactions
        WHERE user_id = %s AND type = 'expense' AND txn_date >= %s
        GROUP BY category
        ORDER BY spent DESC
        """,
        (user_id, this_month_start),
    )

    history_rows = query(
        """
        SELECT period, start_date, end_date, total_amount
        FROM budget_history
        WHERE user_id = %s
        ORDER BY start_date DESC
        LIMIT 12
        """,
        (user_id,),
    )
    budget_history = []
    for row in history_rows:
        spent_row = query(
            """
            SELECT COALESCE(SUM(amount), 0) AS spent
            FROM transactions
            WHERE user_id = %s AND type = 'expense' AND txn_date BETWEEN %s AND %s
            """,
            (user_id, row["start_date"], row["end_date"]),
            one = True,
        )
        budget_history.append({
            "period": row["period"],
            "start": row["start_date"],
            "end": row["end_date"],
            "total": row["total_amount"],
            "spent": spent_row["spent"],
            "over": spent_row["spent"] > row["total_amount"],
        })

    return render_template(
        "statistics.html",
        month_labels=[m["label"] for m in monthly_summary],
        month_income = [float(m["income"]) for m in monthly_summary],
        month_expenses = [float(m["expenses"]) for m in monthly_summary],
        monthly_summary = monthly_summary,
        category_labels = [row["category"] for row in category_rows],
        category_values = [float(row["spent"]) for row in category_rows],
        current_budget = get_budget_status(user_id),
        budget_history = budget_history,
    )

@app.route("/loan-calculator")
@login_required
def loan_calculator():
    return render_template("loan.html")

if __name__ == "__main__":
    app.run(debug=True)
    