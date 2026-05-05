from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from datetime import datetime, timedelta
import requests as http_requests

from config import SECRET_KEY, ADMIN_PASSWORD, LINE_NOTIFY_TOKEN, SUBMIT_COOLDOWN_SECONDS
from database import get_db, init_db

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def send_line_notify(message: str) -> bool:
    if not LINE_NOTIFY_TOKEN:
        app.logger.warning("LINE_NOTIFY_TOKEN not configured")
        return False
    try:
        resp = http_requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"},
            data={"message": message},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        app.logger.error(f"LINE Notify error: {e}")
        return False


def build_line_message(items: list[dict], staff_name: str) -> str:
    now = datetime.now().strftime("%H:%M")
    lines = ["\nKhao Soi Sao Haa 🍜\n", "📋 Stock Alert:"]
    for item in items:
        lines.append(
            f"- {item['name_en']} ({item['name_th']}): "
            f"เหลือ {item['current_qty']} {item['unit']} "
            f"→ ซื้อ {item['need_qty']} {item['unit']}"
        )
    lines.append(f"\nรายงานโดย: {staff_name}")
    lines.append(f"เวลา: {now}")
    return "\n".join(lines)


def check_cooldown() -> bool:
    """Return True if last submission was too recent (within cooldown)."""
    db = get_db()
    row = db.execute(
        "SELECT created_at FROM purchase_requests ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()
    if row is None:
        return False
    last_time = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - last_time).total_seconds() < SUBMIT_COOLDOWN_SECONDS


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


# ──────────────────────────────────────────────────────────────
# Staff routes
# ──────────────────────────────────────────────────────────────

@app.route("/")
def staff_index():
    db = get_db()
    ingredients = db.execute(
        """SELECT i.id, i.name_th, i.name_en, i.unit, i.par_level, i.min_level,
                  c.name_th AS cat_th
           FROM ingredients i
           JOIN categories c ON c.id = i.category_id
           WHERE i.active = 1
           ORDER BY c.id, i.name_th"""
    ).fetchall()
    db.close()
    return render_template("staff/index.html", ingredients=ingredients)


@app.route("/submit", methods=["POST"])
def staff_submit():
    staff_name = request.form.get("staff_name", "").strip()
    if not staff_name:
        flash("กรุณาใส่ชื่อพนักงาน", "error")
        return redirect(url_for("staff_index"))

    db = get_db()
    ingredients = db.execute(
        "SELECT id, name_th, name_en, unit, par_level, min_level FROM ingredients WHERE active=1"
    ).fetchall()

    alert_items = []
    has_any_input = False

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for ing in ingredients:
        qty_key = f"qty_{ing['id']}"
        buy_key = f"buy_{ing['id']}"
        note_key = f"note_{ing['id']}"

        raw_qty = request.form.get(qty_key, "").strip()
        if not raw_qty:
            continue

        try:
            current_qty = float(raw_qty)
        except ValueError:
            continue

        has_any_input = True
        raw_buy = request.form.get(buy_key, "").strip()
        estimate_buy = float(raw_buy) if raw_buy else max(0.0, ing["par_level"] - current_qty)
        note = request.form.get(note_key, "").strip() or None

        db.execute(
            """INSERT INTO stock_logs (ingredient_id, current_qty, estimate_buy, note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ing["id"], current_qty, estimate_buy, note, staff_name, now_str),
        )

        if current_qty <= ing["min_level"]:
            need_qty = max(0.0, ing["par_level"] - current_qty)
            alert_items.append({
                "name_th": ing["name_th"],
                "name_en": ing["name_en"],
                "unit": ing["unit"],
                "current_qty": current_qty,
                "need_qty": round(need_qty, 2),
                "suggest_qty": round(estimate_buy, 2),
            })

    if not has_any_input:
        flash("กรุณาใส่ข้อมูลอย่างน้อย 1 รายการ", "error")
        db.close()
        return redirect(url_for("staff_index"))

    # Create purchase request if there are alert items
    line_sent = False
    if alert_items:
        too_soon = check_cooldown()
        cur = db.execute(
            "INSERT INTO purchase_requests (created_by, status, created_at) VALUES (?, 'pending', ?)",
            (staff_name, now_str),
        )
        request_id = cur.lastrowid

        for item in alert_items:
            db.execute(
                """INSERT INTO purchase_items (request_id, ingredient_id, need_qty, suggest_qty)
                   SELECT ?, i.id, ?, ?
                   FROM ingredients i WHERE i.name_th = ? LIMIT 1""",
                (request_id, item["need_qty"], item["suggest_qty"], item["name_th"]),
            )

        db.commit()

        if not too_soon:
            msg = build_line_message(alert_items, staff_name)
            line_sent = send_line_notify(msg)
            if line_sent:
                db.execute(
                    "UPDATE purchase_requests SET status='sent' WHERE id=?", (request_id,)
                )
                db.commit()
        else:
            db.execute(
                "UPDATE purchase_requests SET status='merged' WHERE id=?", (request_id,)
            )
            db.commit()
    else:
        db.commit()

    db.close()

    flash_msg = "บันทึกสต๊อกเรียบร้อยแล้ว ✓"
    if alert_items:
        if line_sent:
            flash_msg += f" | ส่ง LINE แจ้งเตือน {len(alert_items)} รายการแล้ว"
        else:
            flash_msg += f" | มี {len(alert_items)} รายการต่ำกว่าเป้าหมาย (LINE ไม่ได้รับการตั้งค่า)"

    flash(flash_msg, "success")
    return redirect(url_for("staff_index"))


# ──────────────────────────────────────────────────────────────
# Admin – auth
# ──────────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("รหัสผ่านไม่ถูกต้อง", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# ──────────────────────────────────────────────────────────────
# Admin – dashboard
# ──────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()

    # Low stock: latest log per ingredient where current_qty <= min_level
    low_stock = db.execute(
        """SELECT i.name_th, i.name_en, i.unit, i.min_level, i.par_level,
                  sl.current_qty, sl.created_by, sl.created_at
           FROM ingredients i
           JOIN stock_logs sl ON sl.id = (
               SELECT id FROM stock_logs WHERE ingredient_id = i.id ORDER BY id DESC LIMIT 1
           )
           WHERE i.active = 1 AND sl.current_qty <= i.min_level
           ORDER BY sl.current_qty ASC"""
    ).fetchall()

    recent_logs = db.execute(
        """SELECT sl.created_at, sl.created_by,
                  COUNT(sl.id) AS items_count
           FROM stock_logs sl
           GROUP BY strftime('%Y-%m-%d %H', sl.created_at), sl.created_by
           ORDER BY sl.created_at DESC
           LIMIT 10"""
    ).fetchall()

    purchase_requests = db.execute(
        """SELECT pr.id, pr.created_at, pr.status, pr.created_by,
                  COUNT(pi.id) AS item_count
           FROM purchase_requests pr
           LEFT JOIN purchase_items pi ON pi.request_id = pr.id
           GROUP BY pr.id
           ORDER BY pr.id DESC
           LIMIT 20"""
    ).fetchall()

    db.close()
    return render_template(
        "admin/dashboard.html",
        low_stock=low_stock,
        recent_logs=recent_logs,
        purchase_requests=purchase_requests,
    )


@app.route("/admin/purchase/<int:req_id>")
@admin_required
def admin_purchase_detail(req_id):
    db = get_db()
    pr = db.execute(
        "SELECT * FROM purchase_requests WHERE id=?", (req_id,)
    ).fetchone()
    items = db.execute(
        """SELECT pi.need_qty, pi.suggest_qty,
                  i.name_th, i.name_en, i.unit
           FROM purchase_items pi
           JOIN ingredients i ON i.id = pi.ingredient_id
           WHERE pi.request_id = ?""",
        (req_id,),
    ).fetchall()
    db.close()
    return render_template("admin/purchase_detail.html", pr=pr, items=items)


@app.route("/admin/purchase/<int:req_id>/done", methods=["POST"])
@admin_required
def admin_purchase_done(req_id):
    db = get_db()
    db.execute("UPDATE purchase_requests SET status='done' WHERE id=?", (req_id,))
    db.commit()
    db.close()
    flash("อัปเดตสถานะเป็น 'จัดซื้อแล้ว' เรียบร้อย", "success")
    return redirect(url_for("admin_dashboard"))


# ──────────────────────────────────────────────────────────────
# Admin – ingredient management
# ──────────────────────────────────────────────────────────────

@app.route("/admin/ingredients")
@admin_required
def admin_ingredients():
    db = get_db()
    ingredients = db.execute(
        """SELECT i.*, c.name_th AS cat_th, c.name_en AS cat_en
           FROM ingredients i
           JOIN categories c ON c.id = i.category_id
           WHERE i.active = 1
           ORDER BY c.id, i.name_th"""
    ).fetchall()
    categories = db.execute("SELECT * FROM categories ORDER BY id").fetchall()
    db.close()
    return render_template(
        "admin/ingredients.html",
        ingredients=ingredients,
        categories=categories,
    )


@app.route("/admin/ingredients/add", methods=["POST"])
@admin_required
def admin_ingredient_add():
    db = get_db()
    try:
        db.execute(
            """INSERT INTO ingredients (name_th, name_en, category_id, unit, par_level, min_level)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                request.form["name_th"].strip(),
                request.form["name_en"].strip(),
                int(request.form["category_id"]),
                request.form["unit"].strip(),
                float(request.form["par_level"]),
                float(request.form["min_level"]),
            ),
        )
        db.commit()
        flash("เพิ่มวัตถุดิบเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {e}", "error")
    db.close()
    return redirect(url_for("admin_ingredients"))


@app.route("/admin/ingredients/<int:ing_id>/edit", methods=["POST"])
@admin_required
def admin_ingredient_edit(ing_id):
    db = get_db()
    try:
        db.execute(
            """UPDATE ingredients
               SET name_th=?, name_en=?, category_id=?, unit=?, par_level=?, min_level=?
               WHERE id=?""",
            (
                request.form["name_th"].strip(),
                request.form["name_en"].strip(),
                int(request.form["category_id"]),
                request.form["unit"].strip(),
                float(request.form["par_level"]),
                float(request.form["min_level"]),
                ing_id,
            ),
        )
        db.commit()
        flash("แก้ไขวัตถุดิบเรียบร้อยแล้ว", "success")
    except Exception as e:
        flash(f"เกิดข้อผิดพลาด: {e}", "error")
    db.close()
    return redirect(url_for("admin_ingredients"))


@app.route("/admin/ingredients/<int:ing_id>/delete", methods=["POST"])
@admin_required
def admin_ingredient_delete(ing_id):
    db = get_db()
    db.execute("UPDATE ingredients SET active=0 WHERE id=?", (ing_id,))
    db.commit()
    db.close()
    flash("ลบวัตถุดิบเรียบร้อยแล้ว", "success")
    return redirect(url_for("admin_ingredients"))


# ──────────────────────────────────────────────────────────────
# API – auto-calculate suggested buy (called by JS)
# ──────────────────────────────────────────────────────────────

@app.route("/api/suggest-buy")
def api_suggest_buy():
    ing_id = request.args.get("id", type=int)
    current = request.args.get("qty", type=float)
    if ing_id is None or current is None:
        return jsonify({"error": "missing params"}), 400
    db = get_db()
    ing = db.execute(
        "SELECT par_level FROM ingredients WHERE id=?", (ing_id,)
    ).fetchone()
    db.close()
    if not ing:
        return jsonify({"error": "not found"}), 404
    need = max(0.0, ing["par_level"] - current)
    return jsonify({"suggest": round(need, 2)})


# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=8080)
