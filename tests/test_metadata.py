from loopgrid_microsoft_agent import VERSION, FRAMEWORK

def test_package_identity():
    assert VERSION == '0.1.0'
    assert FRAMEWORK == 'microsoft-agent-framework'
