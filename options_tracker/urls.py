from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/telegram/ingest/", views.telegram_ingest_api, name="telegram_ingest_api"),
    path("options/", views.options_tracker, name="options_tracker"),
    path("scanners/", views.scanners, name="scanners"),
    path("telegram/", views.telegram_feed, name="telegram_feed"),
    path("recommendations/", views.recommendations, name="recommendations"),
    path("index-oi/", views.index_oi, name="index_oi"),
    path("dhan-orders/", views.dhan_orders, name="dhan_orders"),
    path("journal/", views.trade_journal, name="trade_journal"),
    path("archive/", views.archive, name="archive"),
]
