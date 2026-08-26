from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0019_alter_alertsettings_options_and_more")]

    operations = [
        migrations.AddField(
            model_name="mediaarchive",
            name="output_storage",
            field=models.CharField(
                blank=True,
                choices=[("seaweed", "SeaweedFS (local VPS)"), ("r2", "Cloudflare R2")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="mediaarchive",
            name="output_bucket",
            field=models.CharField(
                blank=True,
                choices=[("media", "Media bucket"), ("output", "Output bucket")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="videorender",
            name="output_storage",
            field=models.CharField(
                blank=True,
                choices=[("seaweed", "SeaweedFS (local VPS)"), ("r2", "Cloudflare R2")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="videorender",
            name="output_bucket",
            field=models.CharField(
                blank=True,
                choices=[("media", "Media bucket"), ("output", "Output bucket")],
                default="",
                max_length=16,
            ),
        ),
    ]
