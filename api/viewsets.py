"""
Tenant-aware DRF ViewSets.

Teen cheezein har ViewSet mein automatically handle hoti hain:
  1. `get_queryset()`  -> Model.objects (tenant-scoped manager). Doosre hospital ka
     row queryset mein aata hi nahi, isliye detail view pe bhi 404 milega.
  2. `perform_create()`-> hospital + created_by khud set (client bhej nahi sakta)
  3. permissions        -> authenticated + same-tenant + role + plan ka `api_access`

Naya resource add karna ho to sirf model + serializer likho, baaki isi base se
inherit ho jaata hai.
"""
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated

from .pagination import StandardPagination
from .permissions import HasApiAccess, IsTenantMember


class TenantModelViewSet(viewsets.ModelViewSet):
    """Base: tenant scoping + default permission stack + search/order/filter."""

    permission_classes = [IsAuthenticated, IsTenantMember, HasApiAccess]
    pagination_class = StandardPagination
    filter_backends = [SearchFilter, OrderingFilter]
    tenant_model = None          # subclass set karta hai
    sets_created_by = True       # model pe `created_by` field hai ya nahi
    # Query params se exact-match filtering: ?status=PAID&doctor=3
    # (django-filter dependency avoid karne ke liye khud ka chhota filter)
    filter_fields = ()

    def get_queryset(self):
        assert self.tenant_model is not None, "tenant_model set karo"
        # objects = TenantManager -> current hospital tak automatically scoped
        qs = self.tenant_model.objects.all()
        return self._apply_query_filters(qs)

    def _apply_query_filters(self, qs):
        """
        `filter_fields` mein likhe params ko exact-match filter banao.
        Unknown/invalid values silently ignore nahi karte - 400 dete hain, warna
        client ko lagega filter lag gaya.
        """
        from rest_framework.exceptions import ValidationError

        for field in self.filter_fields:
            if field not in self.request.query_params:
                continue
            value = self.request.query_params[field]
            try:
                qs = qs.filter(**{field: value})
            except (ValueError, TypeError) as exc:
                raise ValidationError({field: f"Invalid value: {value}"}) from exc
        return qs

    def get_hospital(self):
        return getattr(self.request, "hospital", None)

    def perform_create(self, serializer):
        kwargs = {}
        hospital = self.get_hospital()
        if hospital is not None:
            kwargs["hospital"] = hospital
        if self.sets_created_by and hasattr(self.tenant_model, "created_by"):
            kwargs["created_by"] = self.request.user
        serializer.save(**kwargs)

    def perform_update(self, serializer):
        # hospital kabhi change nahi hone dena (row ko doosre tenant mein move karna)
        serializer.save()
