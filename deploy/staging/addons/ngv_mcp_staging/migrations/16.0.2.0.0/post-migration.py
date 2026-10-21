from odoo import SUPERUSER_ID, api

from odoo.addons.ngv_mcp_staging.hooks import ensure_staging_writer


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    ensure_staging_writer(env)
