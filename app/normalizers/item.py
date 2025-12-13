from typing import Optional, Any
from pydantic import BaseModel


class DealItem(BaseModel):
    source: str

    external_id: str
    title: str

    price: float
    currency: str

    url: str
    image_url: Optional[str] = None

    seller_name: Optional[str] = None
    location: Optional[str] = None

    raw: Optional[Any] = None
