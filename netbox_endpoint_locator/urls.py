from django.urls import path
from .views import EndpointLookupView

app_name = "netbox_endpoint_locator"

urlpatterns = [
    path("lookup/", EndpointLookupView.as_view(), name="lookup"),
]