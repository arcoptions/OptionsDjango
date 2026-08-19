from django.urls import path

from . import research_views, stock_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/telegram/ingest/", views.telegram_ingest_api, name="telegram_ingest_api"),
    path("api/telegram/status/", views.telegram_tracker_status_api, name="telegram_tracker_status_api"),
    path("api/options/live/", views.option_live_prices, name="option_live_prices"),
    path("api/market-ticker/", views.market_ticker_api, name="market_ticker_api"),
    path("options/", views.options_tracker, name="options_tracker"),
    path("settings/dhan/", views.dhan_settings, name="dhan_settings"),
    path("options/<int:signal_id>/edit/", views.option_edit, name="option_edit"),
    path("options/<int:signal_id>/delete/", views.option_delete, name="option_delete"),
    path("scanners/", views.scanners, name="scanners"),
    path("telegram/", views.telegram_feed, name="telegram_feed"),
    path("recommendations/", views.recommendations, name="recommendations"),
    path("index-oi/", views.index_oi, name="index_oi"),
    path("nifty-live/", views.nifty_live, name="nifty_live"),
    path("api/nifty-live/status/", views.nifty_live_status_api, name="nifty_live_status_api"),
    path("dhan-orders/", views.dhan_orders, name="dhan_orders"),
    path("journal/", views.trade_journal, name="trade_journal"),
    path("archive/", views.archive, name="archive"),
    path("stocks/", stock_views.stock_options, name="stock_options"),
    path("stocks/<str:symbol>/", stock_views.stock_option_detail, name="stock_option_detail"),
    path("breakeven/", stock_views.breakeven, name="breakeven"),
    path("research/", research_views.research_report, name="research_report"),
]
