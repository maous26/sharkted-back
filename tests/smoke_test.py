"""
Smoke Tests - Vérifie que les 4 sources collectent correctement.
Usage: python tests/smoke_test.py
"""
import sys
import time
from dataclasses import dataclass
from typing import Optional

# Add parent dir to path for imports
sys.path.insert(0, "/app")

from app.collectors.sources.courir import fetch_courir_product
from app.collectors.sources.footlocker import fetch_footlocker_product
from app.collectors.sources.size import fetch_size_product
from app.collectors.sources.jdsports import fetch_jdsports_product


@dataclass
class TestResult:
    source: str
    success: bool
    duration_ms: float
    title: Optional[str] = None
    price: Optional[float] = None
    error: Optional[str] = None


# Test URLs - Produits actifs (mis à jour 13 déc 2025)
TEST_URLS = {
    "courir": "https://www.courir.com/fr/p/adidas-originals-campus-00s-1603344.html",
    "footlocker": "https://www.footlocker.fr/fr/product/~/314217910604.html",
    "size": "https://www.size.co.uk/product/white-nike-x-size-air-max-90/19729396/",
    "jdsports": "https://www.jdsports.fr/product/noir-asics-gel-nyc/19727805_jdsportsfr/",
}

COLLECTORS = {
    "courir": fetch_courir_product,
    "footlocker": fetch_footlocker_product,
    "size": fetch_size_product,
    "jdsports": fetch_jdsports_product,
}


def test_source(source: str) -> TestResult:
    """Test un collector et retourne le résultat."""
    url = TEST_URLS[source]
    collector = COLLECTORS[source]

    start = time.perf_counter()
    try:
        item = collector(url)
        duration_ms = (time.perf_counter() - start) * 1000
        return TestResult(
            source=source,
            success=True,
            duration_ms=duration_ms,
            title=item.title[:50] if item.title else None,
            price=item.price,
        )
    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        return TestResult(
            source=source,
            success=False,
            duration_ms=duration_ms,
            error=str(e)[:100],
        )


def main():
    print("\n" + "=" * 60)
    print("SMOKE TESTS - 4 Sources")
    print("=" * 60 + "\n")

    results = []

    for source in ["courir", "footlocker", "size", "jdsports"]:
        print(f"Testing {source}...", end=" ", flush=True)
        result = test_source(source)
        results.append(result)

        if result.success:
            print(f"✓ OK ({result.duration_ms:.0f}ms) - {result.title} @ {result.price}")
        else:
            print(f"✗ FAIL ({result.duration_ms:.0f}ms) - {result.error}")

    # Summary
    print("\n" + "-" * 60)
    passed = sum(1 for r in results if r.success)
    total = len(results)

    print(f"\nResults: {passed}/{total} sources OK")

    if passed == total:
        print("✓ All smoke tests PASSED")
        return 0
    else:
        print("✗ Some tests FAILED")
        return 1


if __name__ == "__main__":
    exit(main())
