from django.db import models, transaction
from django.conf import settings

from tenants.context import get_current_hospital


class IDCounter(models.Model):
    """
    Dedicated counter rows for generating sequential human-readable IDs
    (PAT-2026-0001, INV-2026-0001, etc) that are 100% safe under concurrent
    requests - even when many staff hit 'register' at the exact same instant.

    Why this exists: locking "the last existing row matching a prefix" doesn't
    work when zero rows exist yet (the very first ID of the year) - there's
    nothing to lock, so concurrent requests can't be serialized. A counter row
    that always exists, locked via select_for_update, solves this completely.

    MULTI-TENANT (Phase 1): the counter is now per-hospital. A new hospital
    starts its numbering at 1, while the old hospital's sequence keeps moving
    forward - that is why the data migration assigns the old counter rows to
    'Default Hospital'. The ID FORMAT stays the same (PAT-2026-0001).
    """
    name = models.CharField(max_length=50)
    hospital = models.ForeignKey(
        'tenants.Hospital', on_delete=models.CASCADE, null=True, blank=True,
        related_name='id_counters',
        help_text='Null = legacy/platform counter (from before multi-tenancy)',
    )
    value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['name', 'hospital'], name='uniq_counter_per_hospital'),
        ]

    @classmethod
    def get_next(cls, name, hospital=None):
        if hospital is None:
            hospital = get_current_hospital()
        with transaction.atomic():
            cls.objects.get_or_create(name=name, hospital=hospital)
            counter = cls.objects.select_for_update().get(name=name, hospital=hospital)
            counter.value += 1
            counter.save(update_fields=['value'])
            return counter.value


class AuditLog(models.Model):
    """
    Tracks who did what to sensitive records (patients, admissions, prescriptions,
    invoices, payments) and when. Important for hospital compliance - if a record
    is ever questioned, there's a paper trail of exactly which staff login touched it.
    Read via Django Admin only; nobody can edit/delete entries through the app.
    """
    class Action(models.TextChoices):
        CREATE = 'CREATE', 'Created'
        UPDATE = 'UPDATE', 'Updated'
        DELETE = 'DELETE', 'Deleted'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey(
        'tenants.Hospital', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='audit_logs',
        help_text="Which hospital's record was touched - for tenant-wise audit trail filtering",
    )
    action = models.CharField(max_length=10, choices=Action.choices)
    model_name = models.CharField(max_length=50)
    object_id = models.CharField(max_length=50)
    object_repr = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['hospital', '-timestamp'])]

    def __str__(self):
        return f"{self.get_action_display()} {self.model_name} #{self.object_id} by {self.user}"
