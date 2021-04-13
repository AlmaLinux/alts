import os

from mako.lookup import TemplateLookup


RESOURCES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'resources')
TEMPLATE_LOOKUP = TemplateLookup(directories=[RESOURCES_DIRECTORY])
