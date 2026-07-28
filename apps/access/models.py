from django.db import models


class ViewerRateLimitBucket(models.Model):
    client_key = models.CharField(max_length=64, unique=True, db_index=True)
    window_started_at = models.DateTimeField()
    failure_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("client_key",)

    def __str__(self) -> str:
        return f"Viewer rate-limit bucket {self.pk}"
