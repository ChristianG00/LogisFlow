import os
import sys

# Le damos el mapa exacto de tus carpetas en AlwaysData
sys.path.append('/home/logisflow/www')
sys.path.append('/home/logisflow/www/env/lib/python3.12/site-packages')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LogisFlow.settings')

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()