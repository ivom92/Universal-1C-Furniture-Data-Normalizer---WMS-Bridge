"""Pydantic v2 data contracts for the 1C v7.7 -> v8 -> WMS pipeline."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RawOrderBlock(BaseModel):
    """Single 3-row item block parsed from a 1C v7.7 picking list."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    line_number: int = Field(..., ge=1, description="№ п/п из колонки A строки 1 блока")
    client_description: str = Field(
        ...,
        description="Клиентское наименование из колонок B-F основной строки",
    )
    item_type: str = Field(..., description="Тип позиции из колонки G (Пачка, Стекло, ...)")
    quantity: int = Field(..., ge=1, description="Количество из колонки H основной строки")
    factory_alias: Optional[str] = Field(
        None,
        description="Фабричный алиас (жёлтая строка, IMP ст...) из колонки B; нет в 1–2-строчных перемещениях",
    )
    order_service_line: str = Field(
        ...,
        description="Служебная строка заказа (Продажи оптовые УРП_... / Заказ ЦНТ-...)",
    )
    excel_row_start: int = Field(..., ge=1, description="Номер первой строки блока в Excel")
    customer_override: Optional[str] = Field(
        None,
        description=(
            "Заказчик/номер заказа для конкретной строки комбинированного документа "
            "(COMPOSITE_PICKING_LIST), если отличается от общего Заказчика документа"
        ),
    )
    is_soft_furniture: bool = Field(
        False,
        description=(
            "True, если блок распарсен из секции мягкой мебели комбинированного "
            "документа — должен идти в обход HybridMatcher (AUTO_NO_BARCODE)"
        ),
    )

    @property
    def order_line_number(self) -> int:
        """Порядковый номер позиции из бланка 1С 7.7 (колонка «№»)."""
        return self.line_number


class V7ParseResult(BaseModel):
    """Result of parsing a 1C v7.7 order workbook."""

    model_config = ConfigDict(str_strip_whitespace=True)

    customer_name: str = Field(
        ...,
        description="ФИО/наименование контрагента из ячейки «Покупатель:»",
    )
    blocks: list[RawOrderBlock] = Field(default_factory=list)
    declared_places: Optional[int] = Field(
        None,
        description="ИТОГО мест из футера бланка (мягкая мебель), если указано",
    )
    checksum_mismatch: bool = Field(
        False,
        description="True, если сумма мест пакетов не совпала с ИТОГО мест",
    )


class CatalogEntity(BaseModel):
    """Single row from the 1C v8 master catalog (17 columns)."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    nomenclature: str = Field(..., alias="Номенклатура")
    characteristic: Optional[str] = Field(None, alias="ХарактеристикаНоменклатуры")
    nomenclature_code: str = Field(..., alias="НоменклатураКод")
    barcode: Optional[str] = Field(None, alias="Штрихкод")
    weight: Optional[float] = Field(None, alias="Вес")
    volume: Optional[float] = Field(None, alias="Объем")
    height: Optional[float] = Field(None, alias="Высота")
    length: Optional[float] = Field(None, alias="Длина")
    depth: Optional[float] = Field(None, alias="Глубина")
    label_model: Optional[str] = Field(None, alias="ЭтикеткаМодель")
    module: Optional[str] = Field(None, alias="Модуль")
    color: Optional[str] = Field(None, alias="Цвет")
    filling: Optional[str] = Field(None, alias="Начинка")
    packaging: Optional[str] = Field(None, alias="Упаковка")
    label_type: Optional[str] = Field(None, alias="ТипЭтикетки")
    ds: Optional[str] = Field(None, alias="ДС")
    storage_zone: Optional[str] = Field(None, alias="ЗонаХранения")

    @field_validator("nomenclature_code", mode="before")
    @classmethod
    def _code_must_be_string(cls, value: object) -> str:
        if value is None:
            raise ValueError("НоменклатураКод is required")
        if isinstance(value, bool):
            raise ValueError("НоменклатураКод must not be boolean")
        return str(value).strip()

    @field_validator("barcode", mode="before")
    @classmethod
    def _barcode_must_be_string_or_none(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("Штрихкод must not be boolean")
        if isinstance(value, float):
            raise ValueError("Штрихкод must be normalized to str before model construction")
        text = str(value).strip()
        return text or None


class ExtractedFeatures(BaseModel):
    """Structured features parsed from a v7.7 order block for catalog matching."""

    model_config = ConfigDict(str_strip_whitespace=True)

    package_ratio: Optional[str] = Field(
        None,
        description="Номер места упаковки: 1/1, 2/2, Ун1/1 и т.д.",
    )
    dimensions: list[str] = Field(
        default_factory=list,
        description="Габариты и погонные размеры: 116х596х16, 839х372, 2,00м",
    )
    alternative_widths: list[int] = Field(
        default_factory=list,
        description="Допустимые ширины из шаблона 160/140/120 → [1600, 1400, 1200]",
    )
    thicknesses: list[str] = Field(
        default_factory=list,
        description="Толщины материала: 4мм, 16мм, 40мм",
    )
    matched_part_types: list[str] = Field(
        default_factory=list,
        description="Типы компонентов, найденные в тексте блока",
    )
    matched_colors: list[str] = Field(
        default_factory=list,
        description="Цвета/декоры из динамического словаря каталога",
    )
    matched_models: list[str] = Field(
        default_factory=list,
        description="Модели/коллекции из динамического словаря каталога",
    )
    sub_brands: set[str] = Field(
        default_factory=set,
        description="Токены подбрендов/линеек (вайт, роял, тренд, ...), найденные в тексте блока",
    )
    is_composite_color: bool = Field(
        False,
        description="True, если в тексте заказа указан составной декор (со слэшем/дефисом), напр. венге/лоредо",
    )


class MatchCandidate(BaseModel):
    """Single catalog candidate with vector similarity and hard-filter status."""

    model_config = ConfigDict(frozen=True)

    catalog_entity: CatalogEntity
    similarity_score: float
    hard_filter_passed: bool = True
    penalty_reason: Optional[str] = None


class LLMResolutionResponse(BaseModel):
    """Structured JSON contract returned by the LLM resolver (Gemini / Ollama)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    selected_nomenclature_code: Optional[str] = Field(
        default=None,
        description=(
            "Точный 11-значный НоменклатураКод выбранного кандидата "
            "из списка или null/None, если подходящего нет"
        ),
    )
    confidence: float = Field(default=0.0, description="Уверенность выбора от 0.0 до 1.0")
    reasoning: str = Field(default="", description="Краткое объяснение выбора на русском")

    @field_validator("selected_nomenclature_code", mode="before")
    @classmethod
    def _code_as_string_or_none(cls, value: object) -> Optional[str]:
        if value is None or value == "null":
            return None
        if isinstance(value, bool):
            raise ValueError("selected_nomenclature_code must not be boolean")
        text = str(value).strip()
        return text or None


class MatchDecision(BaseModel):
    """Intermediate matching result for one v7.7 order block."""

    model_config = ConfigDict(frozen=True)

    raw_block: RawOrderBlock
    extracted_features: ExtractedFeatures
    status: str  # "MATCHED_AUTO", "MATCHED_LLM", "NEEDS_LLM", "QUARANTINE"
    matched_entity: Optional[CatalogEntity] = None
    confidence_score: float = 0.0
    candidates: List[MatchCandidate] = Field(default_factory=list)
    match_method: Optional[str] = Field(
        None,
        description=(
            "Способ сопоставления: exact_article, vector_auto, AUTO_NO_BARCODE, "
            "LLM_GEMINI, LLM_NO_BARCODE, LLM_TIMEOUT, ..."
        ),
    )
    status_detail: Optional[str] = Field(
        None,
        description="Пояснение карантина или отказа LLM для оператора",
    )

    @property
    def order_line_number(self) -> int:
        return self.raw_block.order_line_number


class MatchedOrderItem(BaseModel):
    """Normalized WMS output row after catalog matching."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    nomenclature: str = Field(..., description="Стандартизированное наименование из 1C v8")
    barcode: Optional[str] = Field(None, description="EAN-13 штрихкод из каталога v8 или пусто")
    quantity: int = Field(..., ge=1, description="Количество из 1C v7.7")
    customer_name: str = Field(..., description="Заказчик из шапки 1C v7.7")
    nomenclature_code: Optional[str] = Field(
        None,
        description="Системный PK фабрики (НоменклатураКод) при успешном сопоставлении",
    )
    match_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    match_reason: Optional[str] = Field(None, description="Причина выбора (LLM / vector / exact)")
    source_block: Optional[RawOrderBlock] = Field(None, description="Исходный блок v7.7")

    @field_validator("barcode", "nomenclature_code", mode="before")
    @classmethod
    def _string_codes(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("Code fields must not be boolean")
        text = str(value).strip()
        return text or None

    @property
    def order_line_number(self) -> int:
        if self.source_block is None:
            raise ValueError("source_block is required for order_line_number")
        return self.source_block.order_line_number
