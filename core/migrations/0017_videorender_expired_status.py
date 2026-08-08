from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_cameradevice_cm4_last_seen_at_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='videorender',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending',    'Pending'),
                    ('processing', 'Processing'),
                    ('ready',      'Ready'),
                    ('failed',     'Failed'),
                    ('expired',    'Expired'),
                ],
                db_index=True,
                default='pending',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='videorender',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
