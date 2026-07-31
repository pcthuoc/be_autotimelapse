# Generated manually to remove legacy UserCameraAccess model

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_clientmembership'),
    ]

    operations = [
        migrations.DeleteModel(
            name='UserCameraAccess',
        ),
    ]
