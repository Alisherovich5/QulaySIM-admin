from django.db import migrations, models
import django.db.models.deletion


def approve_existing(apps, schema_editor):
    Testimonial = apps.get_model("content", "Testimonial")
    Testimonial.objects.update(moderation_status="approved")


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0001_initial"),
        ("content", "0002_benefit_device_promobanner_testimonial_faq_answer_ru_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="testimonial",
            name="customer",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="testimonial", to="customers.customer"),
        ),
        migrations.AddField(
            model_name="testimonial",
            name="moderation_status",
            field=models.CharField(choices=[("pending", "Pending review"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending", max_length=10),
        ),
        migrations.AddField(
            model_name="testimonial",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.RunPython(approve_existing, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="testimonial",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
