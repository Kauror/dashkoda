"""The staff-only data-entry hub.

One page listing every workflow that writes domain data from a browser. It is
**not** a second admin site: it lives inside `/admin/`, it is wrapped in
`admin.site.admin_view` like every other custom admin route, and it introduces
no password, no permission model and no session of its own. A viewer holding
only the shared PIN reaches the admin login and stops there.

The page deliberately does not restate Django's model app list. That list is
already on the admin index and answers a different question — "what tables
exist" rather than "what am I here to type in".
"""

from __future__ import annotations

from django.contrib import admin
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .data_entry import available_modules


@require_GET
def data_entry_hub(request):
    return render(
        request,
        "core/admin/data_entry_hub.html",
        {
            **admin.site.each_context(request),
            "title": "Andmete sisestamine",
            "modules": available_modules(),
        },
    )
