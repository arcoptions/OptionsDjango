from django.contrib import admin

from .models import AppSetting, ChartinkTrigger, DhanOrderEvent, IndexOISnapshot, TipSignal, TradeExecution


admin.site.register(TipSignal)
admin.site.register(ChartinkTrigger)
admin.site.register(TradeExecution)
admin.site.register(IndexOISnapshot)
admin.site.register(AppSetting)
admin.site.register(DhanOrderEvent)
