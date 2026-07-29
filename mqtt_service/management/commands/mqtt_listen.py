"""Chạy MQTT listener (long-running): python manage.py mqtt_listen"""
import logging

from django.core.management.base import BaseCommand

from mqtt_service import listener


class Command(BaseCommand):
    help = "Chạy MQTT listener: subscribe camera/# và cập nhật DB."

    def add_arguments(self, parser):
        parser.add_argument("--skip-acl", action="store_true",
                            help="Bỏ qua bước thiết lập ACL trên broker.")

    def handle(self, *args, **options):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        if options["skip_acl"]:
            from django.conf import settings
            settings.MQTT_SKIP_ACL_SETUP = True
        listener.run()
