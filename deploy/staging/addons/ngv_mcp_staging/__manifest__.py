{
    "name": "Ngoc Viet MCP staging fixtures",
    "version": "16.0.1.0.0",
    "license": "MIT",
    "depends": ["crm", "sale_management"],
    "data": [
        "security/ngv_groups.xml",
        "security/ir.model.access.csv",
        "security/ngv_rules.xml",
        "data/fixtures.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
