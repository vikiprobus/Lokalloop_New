from apps.shops.management.commands.seed_demo import Command as SeedDemoCommand

class Command(SeedDemoCommand):
    help = "Alias for seed_demo: Seeds realistic production-like demo data for LokalLoop platform"
