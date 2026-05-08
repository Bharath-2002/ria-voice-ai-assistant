"""Product entity — field names match verified BlueStone API schema."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Product:
    """BlueStone jewelry product.

    Fields map directly to the BlueStone Search API response:
    - id          ← designId
    - name        ← designName
    - description ← shortDesc
    - price       ← float(defaultSkuPrice)
    - metal       ← metalName (search) / metal (details)
    - image_url   ← imageUrl (already a full URL)
    - product_url ← "https://www.bluestone.com/" + productPageUrl
    - carat       ← diamondCarat (details endpoint only)
    - collection  ← collectionName (details endpoint only)
    """

    id: int
    name: str
    description: str
    price: float
    metal: Optional[str] = None
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    carat: Optional[float] = None
    collection: Optional[str] = None
