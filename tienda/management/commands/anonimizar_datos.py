from django.core.management.base import BaseCommand

from tienda.privacidad import anonimizar_datos_vencidos


class Command(BaseCommand):
    help = 'Anonimiza datos personales que superaron el plazo de retención definido.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar',
            action='store_true',
            help='Ejecuta los cambios. Sin esta opción solo muestra el resultado esperado.',
        )

    def handle(self, *args, **options):
        aplicar = options['aplicar']
        resultado = anonimizar_datos_vencidos(aplicar=aplicar)
        prefijo = 'Anonimizados' if aplicar else 'Se anonimizarían'
        self.stdout.write(
            f"{prefijo}: {resultado['pagos_sin_compra']} pagos sin compra, "
            f"{resultado['pagos_confirmados']} pagos confirmados y "
            f"{resultado['clientes']} clientes."
        )
