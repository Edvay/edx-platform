"""
Slightly customized python-social-auth backend for SAML 2.0 support
"""
import logging
from copy import deepcopy

import requests
from django.contrib.sites.models import Site
from django.http import Http404
from django.utils.functional import cached_property
from django_countries import countries
from social_core.backends.saml import OID_EDU_PERSON_ENTITLEMENT, SAMLAuth, SAMLIdentityProvider
from social_core.exceptions import AuthForbidden

from openedx.core.djangoapps.theming.helpers import get_current_request

STANDARD_SAML_PROVIDER_KEY = 'standard_saml_provider'
SAP_SUCCESSFACTORS_SAML_KEY = 'sap_success_factors'
log = logging.getLogger(__name__)


class SAMLAuthBackend(SAMLAuth):  # pylint: disable=abstract-method
    """
    Customized version of SAMLAuth that gets the list of IdPs from third_party_auth's list of
    enabled providers.
    """
    name = "tpa-saml"

    def get_idp(self, idp_name):
        """ Given the name of an IdP, get a SAMLIdentityProvider instance """
        from .models import SAMLProviderConfig
        return SAMLProviderConfig.current(idp_name).get_config()

    def setting(self, name, default=None):
        """ Get a setting, from SAMLConfiguration """
        try:
            return self._config.get_setting(name)
        except KeyError:
            return self.strategy.setting(name, default, backend=self)

    def auth_url(self):
        """
        Check that SAML is enabled and that the request includes an 'idp'
        parameter before getting the URL to which we must redirect in order to
        authenticate the user.

        raise Http404 if SAML authentication is disabled.
        """
        if not self._config.enabled:
            log.error('SAML authentication is not enabled')
            raise Http404

        return super(SAMLAuthBackend, self).auth_url()

    def _check_entitlements(self, idp, attributes):
        """
        Check if we require the presence of any specific eduPersonEntitlement.

        raise AuthForbidden if the user should not be authenticated, or do nothing
        to allow the login pipeline to continue.
        """
        if "requiredEntitlements" in idp.conf:
            entitlements = attributes.get(OID_EDU_PERSON_ENTITLEMENT, [])
            for expected in idp.conf['requiredEntitlements']:
                if expected not in entitlements:
                    log.warning(
                        "SAML user from IdP %s rejected due to missing eduPersonEntitlement %s", idp.name, expected)
                    raise AuthForbidden(self)

    def _create_saml_auth(self, idp):
        """
        Get an instance of OneLogin_Saml2_Auth

        idp: The Identity Provider - a social_core.backends.saml.SAMLIdentityProvider instance
        """
        # We only override this method so that we can add extra debugging when debug_mode is True
        # Note that auth_inst is instantiated just for the current HTTP request, then is destroyed
        auth_inst = super(SAMLAuthBackend, self)._create_saml_auth(idp)
        from .models import SAMLProviderConfig
        if SAMLProviderConfig.current(idp.name).debug_mode:

            def wrap_with_logging(method_name, action_description, xml_getter):
                """ Wrap the request and response handlers to add debug mode logging """
                method = getattr(auth_inst, method_name)

                def wrapped_method(*args, **kwargs):
                    """ Wrapped login or process_response method """
                    result = method(*args, **kwargs)
                    log.info("SAML login %s for IdP %s. XML is:\n%s", action_description, idp.name, xml_getter())
                    return result
                setattr(auth_inst, method_name, wrapped_method)

            wrap_with_logging("login", "request", auth_inst.get_last_request_xml)
            wrap_with_logging("process_response", "response", auth_inst.get_last_response_xml)

        return auth_inst

    @cached_property
    def _config(self):
        from .models import SAMLConfiguration
        return SAMLConfiguration.current(Site.objects.get_current(get_current_request()))


class EdXSAMLIdentityProvider(SAMLIdentityProvider):
    """
    Customized version of SAMLIdentityProvider that can retrieve details beyond the standard
    details supported by the canonical upstream version.
    """

    def get_user_details(self, attributes):
        """
        Overrides `get_user_details` from the base class; retrieves those details,
        then updates the dict with values from whatever additional fields are desired.
        """
        details = super(EdXSAMLIdentityProvider, self).get_user_details(attributes)
        extra_field_definitions = self.conf.get('extra_field_definitions', [])
        details.update({
            field['name']: attributes[field['urn']][0] if field['urn'] in attributes else None
            for field in extra_field_definitions
        })
        return details


class SapSuccessFactorsIdentityProvider(EdXSAMLIdentityProvider):
    """
    Customized version of EdXSAMLIdentityProvider that knows how to retrieve user details
    from the SAPSuccessFactors OData API, rather than parse them directly off the
    SAML assertion that we get in response to a login attempt.
    """

    required_variables = (
        'sapsf_oauth_root_url',
        'sapsf_private_key',
        'odata_api_root_url',
        'odata_company_id',
        'odata_client_id',
    )

    # Define the relationships between SAPSF record fields and Open edX logistration fields.
    default_field_mapping = {
        'username': 'username',
        'firstName': 'first_name',
        'lastName': 'last_name',
        'defaultFullName': 'fullname',
        'email': 'email',
        'country': 'country',
        'city': 'city',
    }

    # Define a simple mapping to relate SAPSF values to Open edX-compatible values for
    # any given field. By default, this only contains the Country field, as SAPSF supplies
    # a country name, which has to be translated to a country code.
    default_value_mapping = {
        'country': {name: code for code, name in countries}
    }

    # Unfortunately, not everything has a 1:1 name mapping between Open edX and SAPSF, so
    # we need some overrides. TODO: Fill in necessary mappings
    default_value_mapping.update({
        'United States': 'US',
    })

    def get_registration_fields(self, response):
        """
        Get a dictionary mapping registration field names to default values.
        """
        field_mapping = self.field_mappings
        registration_fields = {edx_name: response['d'].get(odata_name, '') for odata_name, edx_name in field_mapping.items()}
        value_mapping = self.value_mappings
        for field, value in registration_fields.items():
            if field in value_mapping and value in value_mapping[field]:
                registration_fields[field] = value_mapping[field][value]
        return registration_fields

    @property
    def field_mappings(self):
        """
        Get a dictionary mapping the field names returned in an SAP SuccessFactors
        user entity to the field names with which those values should be used in
        the Open edX registration form.
        """
        overrides = self.conf.get('sapsf_field_mappings', {})
        base = self.default_field_mapping.copy()
        base.update(overrides)
        return base

    @property
    def value_mappings(self):
        """
        Get a dictionary mapping of field names to override objects which each
        map values received from SAP SuccessFactors to values expected in the
        Open edX platform registration form.
        """
        overrides = self.conf.get('sapsf_value_mappings', {})
        base = deepcopy(self.default_value_mapping)
        for field, override in overrides.items():
            if field in base:
                base[field].update(override)
            else:
                base[field] = override[field]
        return base

    @property
    def timeout(self):
        """
        The number of seconds OData API requests should wait for a response before failing.
        """
        return self.conf.get('odata_api_request_timeout', 10)

    @property
    def sapsf_idp_url(self):
        return self.conf['sapsf_oauth_root_url'] + 'idp'

    @property
    def sapsf_token_url(self):
        return self.conf['sapsf_oauth_root_url'] + 'token'

    @property
    def sapsf_private_key(self):
        return self.conf['sapsf_private_key']

    @property
    def odata_api_root_url(self):
        return self.conf['odata_api_root_url']

    @property
    def odata_company_id(self):
        return self.conf['odata_company_id']

    @property
    def odata_client_id(self):
        return self.conf['odata_client_id']

    def invalid_configuration(self):
        """
        Check that we have all the details we need to properly retrieve rich data from the
        SAP SuccessFactors BizX OData API. If we don't, then we should log a warning indicating
        the specific variables that are missing.
        """
        if not all(var in self.conf for var in self.required_variables):
            missing = [var for var in self.required_variables if var not in self.conf]
            log.warning(
                "To retrieve rich user data for an SAP SuccessFactors identity provider, the following keys in "
                "'other_settings' are required, but were missing: %s",
                missing
            )
            return missing

    def log_bizx_api_exception(self, transaction_data, err):
        sys_msg = err.response.json() if err.response else 'Not available'
        headers = err.response.headers if err.response else 'Not available'
        token_data = transaction_data.get('token_data')
        token_data = token_data if token_data else 'Not available'
        log_msg_template = (
            'SAPSuccessFactors exception received for {operation_name} request.  ' +
            'URL: {url}  ' +
            'Company ID: {company_id}.  ' +
            'User ID: {user_id}.  ' +
            'Error message: {err_msg}.  ' +
            'System message: {sys_msg}.  ' +
            'Headers: {headers}.  ' +
            'Token Data: {token_data}.'
        )
        log_msg = log_msg_template.format(
            operation_name=transaction_data['operation_name'],
            url=transaction_data['endpoint_url'],
            company_id=transaction_data['company_id'],
            user_id=transaction_data['user_id'],
            err_msg=err.message,
            sys_msg=sys_msg,
            headers=headers,
            token_data=token_data,
        )
        log.warning(log_msg, exc_info=True)

    def generate_bizx_oauth_api_saml_assertion(self, user_id):
        """
        Obtain a SAML assertion from the SAP SuccessFactors BizX OAuth2 identity provider service using
        information specified in the third party authentication configuration "Advanced Settings" section.
        """
        session = requests.Session()
        transaction_data = {
            'operation_name': 'generate_bizx_oauth_api_saml_assertion',
            'endpoint_url': self.sapsf_idp_url,
            'token_url': self.sapsf_token_url,
            'company_id': self.odata_company_id,
            'client_id': self.odata_client_id,
            'user_id': user_id,
            'private_key': self.sapsf_private_key,
        }
        try:
            assertion = session.post(
                transaction_data['endpoint_url'],
                data=transaction_data,
                timeout=self.timeout,
            )
            assertion.raise_for_status()
        except requests.RequestException as err:
            self.log_bizx_api_exception(transaction_data, err)
            return None
        return assertion.text

    def generate_bizx_oauth_api_access_token(self, user_id):
        """
        Request a new access token from the SuccessFactors BizX OAuth2 identity provider service
        using a valid SAML assertion (see generate_bizx_api_saml_assertion) and the infomration specified
        in the third party authentication configuration "Advanced Settings" section.
        """
        session = requests.Session()
        transaction_data = {
            'operation_name': 'generate_bizx_oauth_api_access_token',
            'endpoint_url': self.sapsf_token_url,
            'client_id': self.odata_client_id,
            'company_id': self.odata_company_id,
            'grant_type': 'urn:ietf:params:oauth:grant-type:saml2-bearer',
        }
        assertion = self.generate_bizx_oauth_api_saml_assertion(user_id)
        if not assertion:
            return None
        try:
            transaction_data['assertion'] = assertion
            token_response = session.post(
                transaction_data['endpoint_url'],
                data=transaction_data,
                timeout=self.timeout,
            )
            token_response.raise_for_status()
        except requests.RequestException as err:
            self.log_bizx_api_exception(transaction_data, err)
            return None
        return token_response.json()

    def get_bizx_odata_api_client(self, user_id):
        session = requests.Session()
        access_token_data = self.generate_bizx_oauth_api_access_token(user_id)
        if not access_token_data:
            return None
        token_string = access_token_data['access_token']
        session.headers.update({'Authorization': 'Bearer {}'.format(token_string), 'Accept': 'application/json'})
        session.token_data = access_token_data
        return session

    def get_user_details(self, attributes):
        """
        Attempt to get rich user details from the SAP SuccessFactors OData API. If we're missing any
        of the info we need to do that, or if the request triggers an exception, then fail nicely by
        returning the basic user details we're able to extract from just the SAML response.
        """
        basic_details = super(SapSuccessFactorsIdentityProvider, self).get_user_details(attributes)
        if self.invalid_configuration():
            return basic_details
        user_id = basic_details['username']
        fields = ','.join(self.field_mappings)
        transaction_data = {
            'operation_name': 'get_user_details',
            'user_id': user_id,
            'company_id': self.odata_company_id,
            'fields': fields,
            'endpoint_url': '{root_url}User(userId=\'{user_id}\')?$select={fields}'.format(
                root_url=self.odata_api_root_url,
                user_id=user_id,
                fields=fields,
            ),
        }
        client = self.get_bizx_odata_api_client(user_id=transaction_data['user_id'])
        if not client:
            return basic_details
        try:
            transaction_data['token_data'] = client.token_data
            response = client.get(
                transaction_data['endpoint_url'],
                timeout=self.timeout,
            )
            response.raise_for_status()
            response = response.json()
        except requests.RequestException as err:
            self.log_bizx_api_exception(transaction_data, err)
            return basic_details
        return self.get_registration_fields(response)


def get_saml_idp_choices():
    """
    Get a list of the available SAMLIdentityProvider subclasses that can be used to process
    SAML requests, for use in the Django administration form.
    """
    return (
        (STANDARD_SAML_PROVIDER_KEY, 'Standard SAML provider'),
        (SAP_SUCCESSFACTORS_SAML_KEY, 'SAP SuccessFactors provider'),
    )


def get_saml_idp_class(idp_identifier_string):
    """
    Given a string ID indicating the type of identity provider in use during a given request, return
    the SAMLIdentityProvider subclass able to handle requests for that type of identity provider.
    """
    choices = {
        STANDARD_SAML_PROVIDER_KEY: EdXSAMLIdentityProvider,
        SAP_SUCCESSFACTORS_SAML_KEY: SapSuccessFactorsIdentityProvider,
    }
    if idp_identifier_string not in choices:
        log.error(
            '%s is not a valid EdXSAMLIdentityProvider subclass; using EdXSAMLIdentityProvider base class.',
            idp_identifier_string
        )
    return choices.get(idp_identifier_string, EdXSAMLIdentityProvider)
