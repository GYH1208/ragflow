from api.db.db_models import Knowledgebase, KnowledgebaseCategory


def test_knowledgebase_category_schema_contract():
    assert KnowledgebaseCategory._meta.table_name == "knowledgebase_category"
    assert (("tenant_id", "name"), True) in KnowledgebaseCategory._meta.indexes
    assert Knowledgebase.category_id.null is True
    assert Knowledgebase.category_id.index is True
