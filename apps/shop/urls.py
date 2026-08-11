from django.urls import path

from .views import shop_overview, shop_product

urlpatterns = [
    path("epood/", shop_overview, name="shop"),
    # The Commerce product ID, never a slug: identity must not move when a
    # product is renamed.
    path("epood/toode/<int:source_product_id>/", shop_product, name="shop-product"),
]
