import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .catalog import DEMO_PRODUCTS, SETTINGS, Product, setting_value
from .config import Mode, ShopError

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    kind TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price INTEGER NOT NULL,
    PRIMARY KEY (kind, quantity)
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    buyer INTEGER NOT NULL,
    kind TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price INTEGER NOT NULL,
    recipient TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'unpaid',
    terms TEXT NOT NULL,
    provider TEXT NOT NULL,
    currency TEXT NOT NULL,
    checkout_id TEXT,
    charge_id TEXT,
    operation_id TEXT UNIQUE,
    transaction_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS order_charge ON orders (provider, charge_id);
CREATE INDEX IF NOT EXISTS orders_queue ON orders (state, created_at);
CREATE INDEX IF NOT EXISTS orders_buyer ON orders (buyer, created_at);
CREATE TABLE IF NOT EXISTS fragment_payments (
    operation_id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_exceptions (
    provider TEXT NOT NULL,
    charge_id TEXT NOT NULL,
    buyer INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    refunded_at TEXT,
    PRIMARY KEY (provider, charge_id)
);
"""

STATUS = {
    "unpaid": "Ожидает оплаты",
    "paid": "Оплачен, ждёт проверки магазина",
    "queued": "В очереди на выдачу",
    "sending": "Выдаётся",
    "review": "На проверке",
    "delivered": "Выдан",
    "refunding": "Возврат обрабатывается",
    "refunded": "Оплата возвращена",
}


@dataclass(frozen=True)
class Order:
    id: str
    buyer: int
    kind: str
    quantity: int
    price: int
    recipient: str
    state: str
    terms: str
    provider: str
    currency: str
    checkout_id: str | None
    charge_id: str | None
    operation_id: str | None
    transaction_id: str | None
    note: str | None
    created_at: str

    @property
    def product(self) -> Product:
        return Product(self.kind, self.quantity, self.price)

    def summary(self, *, admin: bool = False) -> str:
        text = (
            f"#{self.id}\n{self.product.label} → @{self.recipient}\n"
            f"{self.price} {self.currency} · {STATUS[self.state]}"
        )
        if admin:
            text += f"\nПокупатель: {self.buyer}"
            if self.operation_id:
                text += f"\nОперация Fragment: {self.operation_id}"
            if self.transaction_id:
                text += f"\nТранзакция: {self.transaction_id}"
            if self.note:
                text += f"\n{self.note}"
        return text


class Store:
    def __init__(self, directory: Path, mode: Mode) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "shop.sqlite3"
        self.mode = mode
        os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
            if row:
                if row["value"] != mode:
                    raise ShopError("Для другого SHOP_MODE укажите отдельный DATA_DIR.")
            else:
                db.execute("INSERT INTO metadata VALUES ('mode', ?)", (mode,))
                db.executemany(
                    "INSERT INTO settings VALUES (?, ?)",
                    [(key, value) for key, (value, _) in SETTINGS.items()],
                )
                if mode != "live":
                    db.executemany(
                        "INSERT INTO products VALUES (?, ?, ?)",
                        [(p.kind, p.quantity, p.price) for p in DEMO_PRODUCTS],
                    )
                    db.execute("UPDATE settings SET value='1' WHERE key='open'")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def bind_bot(self, bot_id: int) -> None:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('bot_id', ?)", (str(bot_id),))
            value = db.execute("SELECT value FROM metadata WHERE key='bot_id'").fetchone()[0]
            if value != str(bot_id):
                raise ShopError("Этот DATA_DIR уже связан с другим Telegram-ботом.")

    def settings(self) -> dict[str, str]:
        with self.connection() as db:
            return dict(db.execute("SELECT key, value FROM settings"))

    def configure(self, key: str, value: str) -> None:
        value = setting_value(key, value)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if key == "open" and value == "1":
                configured = dict(db.execute("SELECT key, value FROM settings"))
                if self.mode == "live" and not (configured["terms"] and configured["support"]):
                    raise ShopError("Перед открытием укажите terms и support.")
                if not db.execute("SELECT 1 FROM products LIMIT 1").fetchone():
                    raise ShopError("Перед открытием добавьте товар.")
            db.execute("UPDATE settings SET value=? WHERE key=?", (value, key))

    def products(self) -> list[Product]:
        with self.connection() as db:
            return [
                Product(*row)
                for row in db.execute("SELECT * FROM products ORDER BY kind DESC, quantity")
            ]

    def list_product(self, product: Product) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT 1 FROM products WHERE kind=? AND quantity=?",
                (product.kind, product.quantity),
            ).fetchone()
            if not existing and db.execute("SELECT count(*) FROM products").fetchone()[0] >= 30:
                raise ShopError("В каталоге может быть до 30 позиций.")
            db.execute(
                "INSERT INTO products VALUES (?, ?, ?) "
                "ON CONFLICT(kind, quantity) DO UPDATE SET price=excluded.price",
                (product.kind, product.quantity, product.price),
            )

    def unlist(self, kind: str, quantity: int) -> None:
        with self.connection() as db:
            if not db.execute(
                "DELETE FROM products WHERE kind=? AND quantity=?", (kind, quantity)
            ).rowcount:
                raise ShopError("Товар не найден.")

    def create_order(
        self,
        buyer: int,
        product: Product,
        recipient: str,
        terms: str,
        *,
        provider: str,
        currency: str,
    ) -> Order:
        order_id = uuid4().hex[:16]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = dict(db.execute("SELECT key, value FROM settings"))
            price = db.execute(
                "SELECT price FROM products WHERE kind=? AND quantity=?",
                (product.kind, product.quantity),
            ).fetchone()
            if settings["open"] != "1":
                raise ShopError("Магазин сейчас закрыт.")
            if price is None or price["price"] != product.price or terms != settings["terms"]:
                raise ShopError("Каталог или условия изменились. Откройте товар заново.")
            row = db.execute(
                "INSERT INTO orders (id,buyer,kind,quantity,price,recipient,terms,provider,currency) "
                "VALUES (?,?,?,?,?,?,?,?,?) RETURNING *",
                (
                    order_id,
                    buyer,
                    product.kind,
                    product.quantity,
                    product.price,
                    recipient,
                    terms,
                    provider,
                    currency,
                ),
            ).fetchone()
            return Order(**row)

    def get(self, order_id: str, *, buyer: int | None = None) -> Order:
        with self.connection() as db:
            row = db.execute("SELECT * FROM orders WHERE id=?", (order_id.lower(),)).fetchone()
        if row is None or (buyer is not None and row["buyer"] != buyer):
            raise ShopError("Заказ не найден.")
        return Order(**row)

    def orders(self, *, buyer: int | None = None, pending: bool = False) -> list[Order]:
        with self.connection() as db:
            if buyer is not None:
                rows = db.execute(
                    "SELECT * FROM orders WHERE buyer=? ORDER BY rowid DESC LIMIT 5", (buyer,)
                )
            elif pending:
                rows = db.execute(
                    "SELECT * FROM orders WHERE state IN ('paid','queued','sending','review','refunding') "
                    "ORDER BY rowid ASC LIMIT 10"
                )
            else:
                rows = db.execute("SELECT * FROM orders ORDER BY rowid DESC LIMIT 10")
            return [Order(**row) for row in rows]

    def reserve_checkout(
        self, order_id: str, buyer: int, price: int, query_id: str, *, provider: str, currency: str
    ) -> bool:
        with self.connection() as db:
            return bool(
                db.execute(
                    "UPDATE orders SET checkout_id=? WHERE id=? AND buyer=? AND price=? AND provider=? AND currency=? "
                    "AND state='unpaid' AND (checkout_id IS NULL OR checkout_id=?) "
                    "AND created_at >= datetime('now', '-30 minutes') "
                    "AND (SELECT value FROM settings WHERE key='open')='1'",
                    (query_id, order_id, buyer, price, provider, currency, query_id),
                ).rowcount
            )

    def accept_payment(
        self, order_id: str, buyer: int, price: int, currency: str, charge: str, *, provider: str
    ) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM orders WHERE provider=? AND charge_id=?", (provider, charge)
            ).fetchone():
                return False
            if db.execute(
                "SELECT 1 FROM payment_exceptions WHERE provider=? AND charge_id=?",
                (provider, charge),
            ).fetchone():
                return False
            changed = db.execute(
                "UPDATE orders SET charge_id=?, state='paid' "
                "WHERE id=? AND buyer=? AND price=? AND provider=? AND currency=? AND state='unpaid' AND checkout_id IS NOT NULL",
                (charge, order_id, buyer, price, provider, currency),
            ).rowcount
            if not changed:
                db.execute(
                    "INSERT INTO payment_exceptions (provider,charge_id,buyer,order_id,currency,amount) "
                    "VALUES (?,?,?,?,?,?)",
                    (provider, charge, buyer, order_id, currency, price),
                )
                raise_after_commit = True
            else:
                raise_after_commit = False
        if raise_after_commit:
            raise ShopError("Платёж сохранён для проверки. Обратитесь в /paysupport.")
        return True

    def approve(self, order_id: str) -> Order:
        return self.transition(order_id, ("paid",), "queued")

    def claim(self, order_id: str | None = None) -> Order | None:
        with self.connection() as db:
            row = db.execute(
                "UPDATE orders SET state='sending' WHERE id=("
                "SELECT id FROM orders WHERE state='queued' AND (? IS NULL OR id=?) "
                "ORDER BY rowid LIMIT 1) RETURNING *",
                (order_id, order_id),
            ).fetchone()
            return Order(**row) if row else None

    def transition(
        self,
        order_id: str,
        previous: tuple[str, ...],
        state: str,
        *,
        transaction_id: str | None = None,
        note: str | None = None,
    ) -> Order:
        slots = ",".join("?" for _ in previous)
        with self.connection() as db:
            row = db.execute(
                f"UPDATE orders SET state=?, transaction_id=coalesce(?,transaction_id), note=? "
                f"WHERE id=? AND state IN ({slots}) RETURNING *",
                (state, transaction_id, note, order_id.lower(), *previous),
            ).fetchone()
            if row is None:
                raise ShopError("Заказ сменил статус или действие для него недоступно.")
            return Order(**row)

    def start_refund(self, order_id: str, *, verified_not_sent: bool = False) -> Order:
        with self.connection() as db:
            row = db.execute(
                "UPDATE orders SET state='refunding' WHERE id=? AND charge_id IS NOT NULL AND "
                "(state IN ('paid','queued','refunding') OR "
                "(state IN ('review','sending') AND (operation_id IS NULL OR ?))) RETURNING *",
                (order_id.lower(), verified_not_sent),
            ).fetchone()
            if row is None:
                raise ShopError("Возврат недоступен. Проверьте статус выдачи через Fragment.")
            return Order(**row)

    def payment_exceptions(self) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM payment_exceptions ORDER BY rowid DESC LIMIT 20"
                )
            ]

    def payment_exception(self, provider: str, charge: str) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM payment_exceptions WHERE provider=? AND charge_id=?",
                (provider, charge),
            ).fetchone()
        if row is None:
            raise ShopError("Платёж не найден в журнале исключений этого провайдера.")
        return row

    def mark_exception_refunded(self, provider: str, charge: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE payment_exceptions SET refunded_at=CURRENT_TIMESTAMP "
                "WHERE provider=? AND charge_id=? AND refunded_at IS NULL",
                (provider, charge),
            )

    def retry_unsent(self, order_id: str) -> Order:
        with self.connection() as db:
            row = db.execute(
                "UPDATE orders SET state='queued', note=NULL WHERE id=? "
                "AND state IN ('sending','review') AND operation_id IS NULL RETURNING *",
                (order_id.lower(),),
            ).fetchone()
            if row is None:
                raise ShopError("Повтор доступен для выдачи без операции в журнале Fragment.")
            return Order(**row)
