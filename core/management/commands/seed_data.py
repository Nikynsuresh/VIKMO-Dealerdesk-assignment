import csv
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import Product, Dealer
from core.mongodb import mirror_product, mirror_dealer, log_audit_event


class Command(BaseCommand):
    help = "Load catalogue.csv and dealers.csv from data/ into database. Safe to run multiple times."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Clean extraneous products and dealers not present in original data files",
        )

    def handle(self, *args, **options):
        data_dir = settings.DATA_DIR
        clean_mode = options.get("clean", False)

        # 1. Seed Products from catalogue.csv
        cat_file = data_dir / "catalogue.csv"
        if not cat_file.exists():
            self.stdout.write(self.style.ERROR(f"Catalogue file not found at {cat_file}"))
            return

        catalogue_skus = set()
        seeded_products = 0
        with open(cat_file, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = row["sku"].strip()
                catalogue_skus.add(sku)
                price = Decimal(str(row["price"])).quantize(Decimal("0.01"))
                stock = int(row.get("stock", 0))

                product, created = Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        "name": row.get("name", "").strip(),
                        "category": row.get("category", "").strip(),
                        "brand": row.get("brand", "").strip(),
                        "vehicle_fitment": row.get("vehicle_fitment", "").strip(),
                        "price": price,
                        "stock": stock,
                        "description": row.get("description", "").strip(),
                    },
                )
                if not created:
                    # Preserve operational stock if product already exists!
                    product.name = row.get("name", "").strip()
                    product.category = row.get("category", "").strip()
                    product.brand = row.get("brand", "").strip()
                    product.vehicle_fitment = row.get("vehicle_fitment", "").strip()
                    product.price = price
                    product.description = row.get("description", "").strip()
                    if clean_mode:
                        product.stock = stock
                    product.save(update_fields=["name", "category", "brand", "vehicle_fitment", "price", "description", * (["stock"] if clean_mode else [])])

                seeded_products += 1

                # Mirror to MongoDB if available
                mirror_product({
                    "sku": product.sku,
                    "name": product.name,
                    "category": product.category,
                    "brand": product.brand,
                    "vehicle_fitment": product.vehicle_fitment,
                    "price": f"{product.price:.2f}",
                    "stock": product.stock,
                    "description": product.description,
                })

        if clean_mode:
            # Remove any products not in catalogue.csv (e.g. from tests or supplier sync)
            extra_products = Product.objects.exclude(sku__in=catalogue_skus)
            extra_count = extra_products.count()
            if extra_count > 0:
                # Remove order items referencing extra products first if any
                from core.models import OrderItem
                OrderItem.objects.filter(product__in=extra_products).delete()
                extra_products.delete()
                self.stdout.write(f"Cleaned {extra_count} non-catalogue products.")

        # 2. Seed Dealers from dealers.csv
        dealers_file = data_dir / "dealers.csv"
        seeded_dealers = 0
        dealer_codes = set()
        if dealers_file.exists():
            with open(dealers_file, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row["dealer_code"].strip()
                    dealer_codes.add(code)
                    credit_val = row.get("credit_limit", "0.00")
                    try:
                        credit_limit = Decimal(str(credit_val)).quantize(Decimal("0.01"))
                    except Exception:
                        credit_limit = Decimal("0.00")

                    dealer, _ = Dealer.objects.update_or_create(
                        dealer_code=code,
                        defaults={
                            "name": row.get("name", "").strip(),
                            "city": row.get("city", "").strip(),
                            "phone": row.get("phone", "").strip(),
                            "email": row.get("email", "").strip(),
                            "gstin": row.get("gstin", "").strip(),
                            "credit_limit": credit_limit,
                         },
                    )
                    seeded_dealers += 1

                    mirror_dealer({
                        "id": dealer.id,
                        "dealer_code": dealer.dealer_code,
                        "name": dealer.name,
                        "city": dealer.city,
                        "phone": dealer.phone,
                        "email": dealer.email,
                        "gstin": dealer.gstin,
                        "credit_limit": f"{dealer.credit_limit:.2f}",
                    })

        if clean_mode:
            extra_dealers = Dealer.objects.exclude(dealer_code__in=dealer_codes)
            extra_count = extra_dealers.count()
            if extra_count > 0:
                extra_dealers.delete()
                self.stdout.write(f"Cleaned {extra_count} non-dataset dealers.")

        log_audit_event("SEED_DATA_COMPLETED", {"products": seeded_products, "dealers": seeded_dealers})
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {seeded_products} products and {seeded_dealers} dealers (PostgreSQL/SQLite + MongoDB synced)."
            )
        )
