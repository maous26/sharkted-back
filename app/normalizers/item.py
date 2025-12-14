from typing import Optional, Any, List
from pydantic import BaseModel


class DealItem(BaseModel):
    source: str

    external_id: str
    title: str

    price: float
    currency: str
    
    # Prix et remise
    original_price: Optional[float] = None
    discount_percent: Optional[float] = None

    url: str
    image_url: Optional[str] = None

    seller_name: Optional[str] = None  # Brand
    location: Optional[str] = None
    
    # Metadata additionnelle
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    color: Optional[str] = None
    gender: Optional[str] = None
    sizes_available: Optional[List[str]] = None

    raw: Optional[Any] = None
