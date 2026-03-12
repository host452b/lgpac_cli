"""
data models representing all entities from the lgpac API.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


@dataclass
class PriceInfo:
    currency: str = "CNY"
    yuan: str = ""
    cent: str = ""
    prefix: str = "¥"
    suffix: str = ""

    @classmethod
    def from_api(cls, data: Optional[Dict]) -> Optional["PriceInfo"]:
        if not data:
            return None
        return cls(
            currency=data.get("currency", "CNY"),
            yuan=data.get("yuanNum", ""),
            cent=data.get("centNum", ""),
            prefix=data.get("prefix", "¥"),
            suffix=data.get("suffix", ""),
        )

    @property
    def display(self) -> str:
        price = self.yuan
        if self.cent:
            price = f"{self.yuan}.{self.cent}"
        return f"{self.prefix}{price}{self.suffix}"

    def to_float(self) -> float:
        try:
            raw = self.yuan
            if self.cent:
                raw = f"{self.yuan}.{self.cent}"
            return float(raw)
        except (ValueError, TypeError):
            return 0.0


@dataclass
class Category:
    code: int
    display_name: str
    name: str
    seq: int = 0

    @classmethod
    def from_api(cls, data: Dict) -> "Category":
        return cls(
            code=data.get("code", 0),
            display_name=data.get("displayName", ""),
            name=data.get("name", ""),
            seq=data.get("seq", 0),
        )


@dataclass
class FrontendCategory:
    """category as configured by the shop (visible tabs on homepage)."""
    biz_id: str
    name: str
    category_codes: List[str] = field(default_factory=list)
    seq: int = 0

    @classmethod
    def from_api(cls, data: Dict) -> "FrontendCategory":
        return cls(
            biz_id=data.get("bizFrontendCategoryId", ""),
            name=data.get("categoryName", ""),
            category_codes=data.get("categoryCodes", []),
            seq=data.get("seq", 0),
        )


@dataclass
class ShowTag:
    title: str
    tag_type: str
    seq: int = 0

    @classmethod
    def from_api(cls, data: Dict) -> "ShowTag":
        return cls(
            title=data.get("title", ""),
            tag_type=data.get("type", ""),
            seq=data.get("seq", 0),
        )


@dataclass
class SeatPlan:
    seat_plan_id: str
    session_id: str
    name: str
    original_price: float = 0.0
    price_info: Optional[PriceInfo] = None
    color: str = ""
    is_combo: bool = False
    combo_display_tag: str = ""
    category: str = "BASE"
    is_stop_sale: bool = False
    can_buy_count: int = -1  # -1 = unknown, 0 = sold out, >0 = available
    combo_items: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Dict) -> "SeatPlan":
        items = []
        for item in data.get("items", []):
            items.append({
                "seat_plan_id": item.get("bizSeatPlanId", ""),
                "name": item.get("itemSeatPlanName", ""),
                "unit_qty": item.get("unitQty", 1),
                "price": item.get("originalPrice", 0.0),
            })

        return cls(
            seat_plan_id=data.get("seatPlanId", ""),
            session_id=data.get("showSessionId", ""),
            name=data.get("seatPlanName", ""),
            original_price=data.get("originalPrice", 0.0),
            price_info=PriceInfo.from_api(data.get("originalPriceVO")),
            color=data.get("colorValue", ""),
            is_combo=data.get("isCombo", False),
            combo_display_tag=data.get("comboDisplayTag", ""),
            category=data.get("seatPlanCategory", "BASE"),
            is_stop_sale=data.get("isStopSale", False),
            combo_items=items,
        )

    @property
    def truly_available(self) -> bool:
        """check real availability using dynamic stock data."""
        if self.is_stop_sale:
            return False
        if self.can_buy_count == 0:
            return False
        return True


@dataclass
class Session:
    session_id: str
    show_id: str
    name: str
    begin_time: Optional[int] = None
    end_time: Optional[int] = None
    has_combo: bool = False
    limitation: int = 0
    seat_plans: List[SeatPlan] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Dict) -> "Session":
        return cls(
            session_id=data.get("bizShowSessionId", ""),
            show_id=data.get("showId", ""),
            name=data.get("sessionName", ""),
            begin_time=data.get("beginDateTime"),
            end_time=data.get("endDateTime"),
            has_combo=data.get("hasCombo", False),
            limitation=data.get("limitation", 0),
        )

    @property
    def begin_datetime(self) -> Optional[datetime]:
        if self.begin_time:
            return datetime.fromtimestamp(self.begin_time / 1000)
        return None

    @property
    def end_datetime(self) -> Optional[datetime]:
        if self.end_time:
            return datetime.fromtimestamp(self.end_time / 1000)
        return None


@dataclass
class ServiceNote:
    name: str
    value: str
    code: str
    enabled: bool = True

    @classmethod
    def from_api(cls, data: Dict) -> "ServiceNote":
        return cls(
            name=data.get("name", ""),
            value=data.get("value", ""),
            code=data.get("code", ""),
            enabled=data.get("type", True),
        )


@dataclass
class Show:
    show_id: str
    name: str
    show_date: str = ""
    city_name: str = ""
    city_id: str = ""
    status: str = ""
    poster_url: str = ""
    poster_color: str = ""
    content_url: str = ""
    venue_id: str = ""
    venue_name: str = ""
    venue_address: str = ""
    venue_lat: float = 0.0
    venue_lng: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    min_price_info: Optional[PriceInfo] = None
    max_price_info: Optional[PriceInfo] = None
    category: Optional[Category] = None
    tags: List[ShowTag] = field(default_factory=list)
    is_free: bool = False
    sold_out: bool = False
    session_count: int = 0
    seat_plan_count: int = 0
    sessions: List[Session] = field(default_factory=list)
    service_notes: List[ServiceNote] = field(default_factory=list)
    first_show_time: Optional[int] = None
    last_show_time: Optional[int] = None
    crawled_at: str = ""

    @classmethod
    def from_search(cls, data: Dict) -> "Show":
        """parse from show_list/search API response."""
        cat_data = data.get("backendCategory")
        category = Category.from_api(cat_data) if cat_data else None
        tags = [ShowTag.from_api(t) for t in data.get("showTags", [])]

        return cls(
            show_id=data.get("showId", ""),
            name=data.get("showName", ""),
            show_date=data.get("showDate", ""),
            city_name=data.get("cityName", ""),
            city_id=data.get("cityId", ""),
            status=data.get("showStatus", ""),
            poster_url=data.get("posterUrl", ""),
            venue_id=data.get("venueId", ""),
            venue_name=data.get("venueName", ""),
            min_price=data.get("minOriginalPrice", 0.0),
            min_price_info=PriceInfo.from_api(data.get("minOriginalPriceInfo")),
            category=category,
            tags=tags,
            is_free=data.get("isFree", False),
            sold_out=data.get("soldOut", False),
            session_count=data.get("sessionNum", 0),
            seat_plan_count=data.get("seatPlanNum", 0),
            first_show_time=data.get("firstShowTime"),
            last_show_time=data.get("lastShowTime"),
        )

    def enrich_from_static(self, data: Dict):
        """merge data from show_static_data API."""
        self.show_date = data.get("showDate", self.show_date)
        self.poster_url = data.get("posterUrl", self.poster_url)
        self.poster_color = data.get("posterColor", "")
        self.content_url = data.get("contentUrl", "")
        self.venue_id = data.get("venueId", self.venue_id)
        self.venue_name = data.get("venueName", self.venue_name)
        self.venue_address = data.get("venueAddress", "")
        self.venue_lat = data.get("venueLat", 0.0)
        self.venue_lng = data.get("venueLng", 0.0)
        self.city_id = data.get("cityId", self.city_id)
        self.city_name = data.get("cityName", self.city_name)

        max_price = PriceInfo.from_api(data.get("maxOriginalPriceInfo"))
        if max_price:
            self.max_price = max_price.to_float()
            self.max_price_info = max_price

        show_type = data.get("showType")
        if show_type and not self.category:
            self.category = Category.from_api(show_type)

    def enrich_from_dynamic(self, data: Dict):
        """merge data from show_dynamic_data API."""
        self.status = data.get("showDetailStatus", self.status)

    def to_dict(self) -> Dict[str, Any]:
        """serialize to a plain dict for JSON output."""
        result = {
            "show_id": self.show_id,
            "name": self.name,
            "show_date": self.show_date,
            "city": self.city_name,
            "status": self.status,
            "poster_url": self.poster_url,
            "content_url": self.content_url,
            "venue": {
                "id": self.venue_id,
                "name": self.venue_name,
                "address": self.venue_address,
                "lat": self.venue_lat,
                "lng": self.venue_lng,
            },
            "price": {
                "min": self.min_price,
                "max": self.max_price,
                "min_display": self.min_price_info.display if self.min_price_info else "",
                "max_display": self.max_price_info.display if self.max_price_info else "",
            },
            "category": {
                "code": self.category.code,
                "name": self.category.display_name,
            } if self.category else None,
            "tags": [{"title": t.title, "type": t.tag_type} for t in self.tags],
            "is_free": self.is_free,
            "sold_out": self.sold_out,
            "session_count": self.session_count,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "name": s.name,
                    "begin_time": str(s.begin_datetime) if s.begin_datetime else None,
                    "end_time": str(s.end_datetime) if s.end_datetime else None,
                    "limitation": s.limitation,
                    "seat_plans": [
                        {
                            "id": sp.seat_plan_id,
                            "name": sp.name,
                            "price": sp.original_price,
                            "is_combo": sp.is_combo,
                            "is_stop_sale": sp.is_stop_sale,
                            "can_buy_count": sp.can_buy_count,
                            "available": sp.truly_available,
                            "combo_items": sp.combo_items,
                        }
                        for sp in s.seat_plans
                    ],
                }
                for s in self.sessions
            ],
            "service_notes": [
                {"name": n.name, "value": n.value, "code": n.code}
                for n in self.service_notes
            ],
            "crawled_at": self.crawled_at,
        }
        return result


@dataclass
class ShopConfig:
    shop_name: str = ""
    shop_color: str = ""
    shop_avatar: str = ""
    intro: str = ""
    icp_license: str = ""
    app_id: str = ""
    frontend_categories: List[FrontendCategory] = field(default_factory=list)
    bottom_navigations: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Dict) -> "ShopConfig":
        cy_config = data.get("cyShopConfigVO", {})

        seen_names = set()
        categories = []
        for c in cy_config.get("showFrontendCategories", []):
            fc = FrontendCategory.from_api(c)
            if fc.name not in seen_names:
                seen_names.add(fc.name)
                categories.append(fc)
        navs = [
            {
                "name": n.get("navigationName", ""),
                "type": n.get("floorType", ""),
                "path": n.get("pagePath", ""),
            }
            for n in cy_config.get("bottomNavigations", [])
        ]

        return cls(
            shop_name=cy_config.get("shopName", ""),
            shop_color=cy_config.get("shopColor", ""),
            shop_avatar=cy_config.get("shopAvatar", ""),
            intro=cy_config.get("intro", ""),
            icp_license=cy_config.get("shopDomainIcpLicense", ""),
            app_id=cy_config.get("appId", ""),
            frontend_categories=categories,
            bottom_navigations=navs,
        )
