import json
import time
import logging
import requests
import zope.interface

from certbot import errors
from certbot import interfaces
from certbot.plugins import dns_common

logger = logging.getLogger(__name__)

@zope.interface.implementer(interfaces.IAuthenticator)
@zope.interface.provider(interfaces.IPluginFactory)
class Authenticator(dns_common.DNSAuthenticator):
    """
    DNS Authenticator for DreamHost

    This Authenticator uses the DreamHost REST API to fulfill a dns-01 challenge
    """

    description = "Obtain certificates using a DNS .txt record"
    ttl = 60

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.credentials = None

    @classmethod
    def add_parser_arguments(cls, add):
        """
        Read config file
        """

        super().add_parser_arguments(
            add, default_propagation_seconds=120
        )
        add("credentials", help="DreamHost credentials .ini file.")

    def more_info(self):  # pylint: disable=missing-docstring
        return (
            "This plugin configures a DNS .txt record to respond to a dns-01 challenge using "
            + "the DreamHost Remote REST API."
        )

    def _setup_credentials(self):
        self.credentials = self._configure_credentials(
            "credentials",
            "DreamHost credentials INI file",
            {
                "baseurl": "URL of the DreamHost Remote API.",
                "api_key": "Password for DreamHost Remote API.",
            },
        )

    def _perform(self, domain, validation_name, validation):
        self._get_dreamhost_client().add_txt_record(
            validation_name, validation
        )

    def _perform(self, domain, validation_name, validation):
        self._get_dreamhost_client().delete_txt_record(
            validation_name, validation
        )

    def _get_dreamhost_client(self):
        return _DreamHostClient(
            self.credentials.conf("baseurl"),
            self.credentials.conf("api_key"),
        )

class _DreamHostClient(object):
    """
    Encapsulates all communication with the DreamHost REST API
    """

    def __init__(self, baseurl, api_key):
        logger.debug("creating dreamhost client")
        self.baseurl = baseurl
        self.api_key = api_key
        self.session = requests.Session()
        self.valid_key = self._test_key()

    def _test_key(self):
        logger.debug("testing api key")

        url = f"{self._get_url('dns-list_records')}&format=json"
        resp = self.session.get(url)

        try:
            result = resp.json()
        except ValueError as exc:
            logger.debug("Error parsing response json | cmd = %s | exception = %s", url, exc)
            return False

        if result["result"] == "error" and result["data"] == "invalid_api_key":
            logger.debug("API key is invalid: %s", self.api_key)
            return False

        logger.debug("the key is valid")
        return True

    def _api_request(self, action):
        if not self.valid_key:
            return None

        url = f"{self._get_url(action)}&format=json"
        resp = self.session.get(url)
        logger.debug("API Request | cmd = %s", url)
        if resp.status_code != 200:
            raise errors.Plurequests.exceptionsginError(
                f"HTTP Error during login {resp.status_code}"
            )
        try:
            result = resp.json()
        except Exception as exp:
            raise Exception(
                f"API response with non JSON: {resp.text} from exp"
            ) from exp

        if result["result"] == "success":
            return result["data"]

        raise errors.PluginError(
            f"API response with an error: {result['data']}"
        )

    def _get_url(self, action):
        return f"{self.baseurl}?key={self.api_key}&cmd={action}"

    def add_txt_record(self, record_name, record_content):
        """
        Add a .txt record to a specific domain

        :param str domain: The domain where the record should be added
        :param str record_name: The record name
        :param str record_content: The record content
        :raises exception if an error occurs communicating with the DreamHost API
        """

        if not self.valid_key:
            raise Exception("The provided key is invalid")

        record = self.get_existing_txt(record_name)
        if record is not None:
            if record["value"] == record_content:
                logger.info("This record already exists. name = %s", record_name)
                return

            logger.info("Updating record %s", record_name)
            # we have to delete the record in order to add a new one with different content
            # DreamHost does not provide any update method (09-11-2022)
            # https://help.dreamhost.com/hc/en-us/articles/217555707-DNS-API-commands
            self.delete_txt_record(record_name, record_content)

        logger.info("Creating a new TXT record")
        self._api_request(f"dns-add_record&record={record_name}&type=TXT&value={record_content}")

    def delete_txt_record(self, record_name, record_content):
        """
        Delete a .txt record from a specific domain

        :param str domain: The domain where the record should be added
        :param str record_name: The record name
        :param str record_content: The record content
        :raises exception if an error occurs communicating with the DreamHost API
        """

        if not self.valid_key:
            raise Exception("The provided key is invalid")

        record = self.get_existing_txt(record_name)
        if record is not None:
            if record["value"] == record_content:
                logger.info("Creating a new TXT record. name = %s", record_name)
                self._api_request(
                    f"dns-remove_record&record={record_name}&type=TXT&value={record_content}"
                )

    def get_existing_txt(self, record_name):
        """
        Searches for an already existing TXT record that contains 
        the same content that we want to store.

        :param str record_name: the record name

        :returns: TXT record value or None
        :rtype: 'string' or 'None'
        """

        if not self.valid_key:
            return None

        records = self._api_request("dns-list_records")
        for record in records:
            if (record["record"] == record_name
                and record["type"] == "TXT"
            ):
                return record

        return None

# https://github.com/m42e/certbot-dns-ispconfig/blob/master/certbot_dns_ispconfig/dns_ispconfig.py
# https://help.dreamhost.com/hc/en-us/articles/217555707-DNS-API-commands
# https://api.dreamhost.com/?key=6SHU5P2HLDAYECU&cmd=dns-list_records&format=json