"""OCI may use short region identifiers in project OCIDs on hosted deployments."""
import pytest
from oci.regions import REGIONS_SHORT_NAMES
from app.enterprise_ai.responses_transport import OCIResponsesTransport, normalize_endpoint


@pytest.mark.parametrize('short,region', sorted(REGIONS_SHORT_NAMES.items()))
def test_regional_project_identifiers_are_accepted_without_rewriting(short, region):
    endpoint = f'https://inference.generativeai.{region}.oci.oraclecloud.com'
    for identifier in (short, region):
        project = f'ocid1.generativeaiproject.oc1.{identifier}.syntheticfixture'
        client = OCIResponsesTransport(endpoint=endpoint, project_id=project)
        assert client.base_url == endpoint + '/openai/v1'
        assert client.project_id == project


@pytest.mark.parametrize('project', [
    'ocid1.generativeaiproject.oc1.iad.syntheticfixture',
    'ocid1.generativeaiproject.oc1.us-ashburn-1.syntheticfixture',
    'ocid1.generativeaiproject.oc1.unknown.syntheticfixture',
    'ocid1.generativeaiproject.oc1.hyd.syntheticfixture/extra',
    'ocid1.compartment.oc1.hyd.syntheticfixture',
])
def test_wrong_region_and_invalid_project_are_rejected(project):
    with pytest.raises(ValueError, match='project'):
        normalize_endpoint('https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com', project)


@pytest.mark.parametrize('endpoint', [
    'http://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com',
    'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com.evil.invalid',
    'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/private',
    'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com?override=true',
    'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com#fragment',
])
def test_endpoint_boundary_is_preserved(endpoint):
    with pytest.raises(ValueError, match='HTTPS endpoint'):
        normalize_endpoint(endpoint, 'ocid1.generativeaiproject.oc1.hyd.syntheticfixture')
