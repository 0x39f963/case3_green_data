"""Deterministic synthetic values for GreenData columns."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from faker import Faker

from scripts._synthetic.introspect import ColumnInfo, TableInfo


EMAIL_DOMAINS = ("example.test", "mail.invalid")
RU_WORDS = (
    "заявка", "кредит", "проверка", "решение", "клиент", "договор", "лимит",
    "офис", "статус", "проект", "согласование", "гарантия", "продукт",
)


class ValueMaker:
    def __init__(self, seed: int, locale: str = "ru_RU") -> None:
        self.rng = random.Random(seed)
        self.fake = Faker(locale)
        Faker.seed(seed)

    def value(self, table: TableInfo, col: ColumnInfo, row_no: int) -> Any:
        tag = table.pii_tags.get(col.name, "")
        name = col.name.lower()
        data_type = col.data_type.lower()

        if tag:
            return self._pii_value(tag, col, row_no)
        if data_type in {"timestamp without time zone", "timestamp with time zone"}:
            return self._timestamp()
        if data_type == "date":
            return self._timestamp().date()
        if data_type == "smallint":
            return self.rng.choices([0, 1, 2, 3], weights=[0.6, 0.25, 0.10, 0.05], k=1)[0]
        if data_type in {"integer", "bigint"}:
            return self._int_value(name, row_no)
        if data_type == "numeric":
            return self._numeric(col)
        if data_type == "boolean":
            return bool(self.rng.getrandbits(1))
        if data_type in {"json", "jsonb"}:
            return {"k": self.fake.word(), "n": self.rng.randint(1, 999)}
        if col.udt_name == "uuid":
            return str(uuid.UUID(int=self.rng.getrandbits(128)))
        return self._text_value(name, col.char_max, row_no)

    def _pii_value(self, tag: str, col: ColumnInfo, row_no: int) -> Any:
        tag_l = tag.lower()
        name = col.name.lower()
        data_type = col.data_type.lower()
        if data_type == "smallint":
            return self.rng.choices([0, 1], weights=[0.8, 0.2], k=1)[0]
        if data_type in {"bigint", "integer"}:
            return self._int_value(name, row_no)
        if data_type == "numeric":
            return self._numeric(col)
        if data_type in {"timestamp without time zone", "timestamp with time zone", "date"}:
            value = self._timestamp()
            return value.date() if data_type == "date" else value
        if "email" in tag_l or "email" in name:
            return "test" + str(row_no) + "@" + self.rng.choice(EMAIL_DOMAINS)
        if "phone" in tag_l or "phone" in name:
            return "+79" + "".join(str(self.rng.randint(0, 9)) for _ in range(9))
        if "inn" in tag_l or name == "inn":
            return self._inn12(row_no)
        return self._fit("TEST-" + tag_l.replace("_", "-") + "-" + str(row_no), col.char_max)

    def _timestamp(self) -> datetime:
        now = datetime.now().replace(microsecond=0)
        if self.rng.random() < 0.1:
            days = self.rng.randint(0, 30)
        else:
            days = self.rng.randint(0, 365 * 5)
        seconds = self.rng.randint(0, 86_399)
        return now - timedelta(days=days, seconds=seconds)

    def _int_value(self, name: str, row_no: int) -> int:
        if "status" in name:
            return self.rng.choices([0, 1, 2, 3], weights=[0.6, 0.25, 0.10, 0.05], k=1)[0]
        if name.endswith("_id") or name in {"type_id", "org_id", "user_id"}:
            return self.rng.randint(1, 1000)
        return row_no

    def _numeric(self, col: ColumnInfo) -> Decimal:
        scale = col.numeric_scale if col.numeric_scale is not None else 2
        value = Decimal(str(round(self.rng.lognormvariate(8, 1), min(scale, 6))))
        if scale <= 0:
            return value.quantize(Decimal("1"))
        return value.quantize(Decimal("1." + ("0" * scale)))

    def _text_value(self, name: str, char_max: int | None, row_no: int) -> str:
        if "email" in name:
            return "test" + str(row_no) + "@" + self.rng.choice(EMAIL_DOMAINS)
        if "phone" in name:
            return "+79" + "".join(str(self.rng.randint(0, 9)) for _ in range(9))
        if "name" in name or "title" in name:
            text = self.fake.company() + " " + str(row_no)
        elif "description" in name or "comment" in name or "txt" in name or char_max is None:
            text = self._long_text(row_no)
        else:
            text = self.fake.catch_phrase() + " " + str(row_no)
        return self._fit(text, char_max)

    def _long_text(self, row_no: int) -> str:
        roll = self.rng.random()
        if roll < 0.4:
            count = self.rng.randint(4, 12)
        elif roll < 0.8:
            count = self.rng.randint(30, 80)
        else:
            count = self.rng.randint(150, 260)
        words = [self.rng.choice(RU_WORDS) for _ in range(count)]
        return " ".join(words) + " " + str(row_no)

    def _fit(self, text: str, char_max: int | None) -> str:
        if char_max is None:
            return text
        return text[:char_max]

    def _inn12(self, row_no: int) -> str:
        base = [int(ch) for ch in f"{row_no % 10_000_000_000:010d}"]
        n11 = sum(a * b for a, b in zip(base, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8])) % 11 % 10
        n12 = sum(a * b for a, b in zip(base + [n11], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8])) % 11 % 10
        return "".join(str(x) for x in base + [n11, n12])
