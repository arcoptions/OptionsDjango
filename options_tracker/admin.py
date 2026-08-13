from django.contrib import admin

from .models import (
	AppSetting,
	ChartinkTrigger,
	ChatMessage,
	DhanOrderEvent,
	IndexOISnapshot,
	IndexOptionCandle,
	IndexOptionStrikeSnapshot,
	TipSignal,
	TradeExecution,
)


admin.site.register(TipSignal)
admin.site.register(ChatMessage)
admin.site.register(ChartinkTrigger)
admin.site.register(TradeExecution)
admin.site.register(IndexOISnapshot)
admin.site.register(IndexOptionStrikeSnapshot)
admin.site.register(IndexOptionCandle)
admin.site.register(AppSetting)
admin.site.register(DhanOrderEvent)
