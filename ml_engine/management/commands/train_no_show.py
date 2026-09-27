"""
Train the GradientBoosting model and save it with joblib.

    python manage.py train_no_show                     # global model (all hospitals)
    python manage.py train_no_show --hospital acme     # only on this hospital's data
    python manage.py train_no_show --min-rows 100      # threshold override (testing)
    python manage.py train_no_show --dry-run           # only report readiness

If data is scarce the command does NOT fail - it clearly reports that the
rule-based engine keeps running (this is the expected cold-start behaviour).
"""
from django.core.management.base import BaseCommand

from ml_engine.predict import readiness
from ml_engine.sklearn_model import train
from tenants.models import Hospital


class Command(BaseCommand):
    help = "Train + save the no-show GradientBoosting model."

    def add_arguments(self, parser):
        parser.add_argument("--hospital", help="Hospital slug (default: global model)")
        parser.add_argument("--min-rows", type=int, help="ML_MIN_TRAINING_ROWS override")
        parser.add_argument("--dry-run", action="store_true", help="Do not train, only report readiness")

    def handle(self, *args, **opts):
        hospital = None
        if opts["hospital"]:
            try:
                hospital = Hospital.objects.get(slug=opts["hospital"])
            except Hospital.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Hospital not found: {opts['hospital']}"))
                return

        info = readiness(hospital)
        scope = hospital.slug if hospital else "global"
        self.stdout.write(f"  scope           : {scope}")
        self.stdout.write(f"  labelled rows   : {info['labelled_rows']}")
        self.stdout.write(f"  no-show rows    : {info['no_show_rows']} ({info['positive_rate']:.1%})")
        self.stdout.write(f"  required        : {info['required_rows']}")

        if opts["dry_run"]:
            self.stdout.write(f"  verdict         : {info['verdict']}")
            return

        if not info["ml_ready"]:
            self.stdout.write(self.style.WARNING(
                f"\n  {info['verdict']}\n"
                "  The rule-based engine (ml_engine.rules) keeps running - no error.\n"
                "  Run this command again once the data grows."
            ))
            return

        result = train(hospital=hospital, min_rows=opts["min_rows"])
        if not result.get("ok"):
            self.stdout.write(self.style.WARNING(f"\n  Not trained: {result['reason']}"))
            return

        metrics = result["metrics"]
        self.stdout.write(self.style.SUCCESS(f"\n  TRAINED: {result['version']}"))
        self.stdout.write(f"    rows            : {metrics['training_rows']}")
        self.stdout.write(f"    ROC-AUC         : {metrics.get('roc_auc', 'n/a')}")
        self.stdout.write(f"    precision@0.5   : {metrics.get('precision', 'n/a')}")
        self.stdout.write(f"    recall@0.5      : {metrics.get('recall', 'n/a')}")
        self.stdout.write(f"    baseline rate   : {metrics.get('baseline_no_show_rate', 'n/a')}")
        self.stdout.write("    (the AUC baseline is 0.5 - this difference shows how much better it is)")
        self.stdout.write(f"    file            : {result['artifact'].relative_path}")
