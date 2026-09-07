from django.contrib import admin

from .models import Allocation, Distribution, ProfitPeriod

admin.site.register(ProfitPeriod)
admin.site.register(Allocation)
admin.site.register(Distribution)
