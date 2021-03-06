from django import forms
from django.conf import settings
from django.forms import ModelForm
from django.forms.models import modelformset_factory

import commonware.log
import happyforms
from quieter_formset.formset import BaseModelFormSet

from addons.models import Addon
from files.models import File
from zadmin.models import SiteEvent


LOGGER_NAME = 'z.zadmin'
log = commonware.log.getLogger(LOGGER_NAME)


class DevMailerForm(happyforms.Form):
    _choices = [('eula',
                 'Developers who have set up EULAs for active add-ons'),
                ('sdk', 'Developers of active SDK add-ons'),
                ('apps', 'Developers of active apps (not add-ons)'),
                ('free_apps_region_enabled',
                 'Developers of free apps and new region enabled'),
                ('free_apps_region_disabled',
                 'Developers of free apps with new regions disabled'),
                ('payments',
                 'Developers of non-deleted apps (not add-ons) with payments'),
                ('payments_region_enabled',
                 'Developers of apps with payments and new regions enabled'),
                ('payments_region_disabled',
                 'Developers of apps with payments and new regions disabled'),
                ('desktop_apps',
                 'Developers of non-deleted apps supported on desktop'),
                ('all_extensions', 'All extension developers')]
    recipients = forms.ChoiceField(choices=_choices, required=True)
    subject = forms.CharField(widget=forms.TextInput(attrs=dict(size='100')),
                              required=True)
    preview_only = forms.BooleanField(initial=True, required=False,
                                      label=u'Log emails instead of sending')
    message = forms.CharField(widget=forms.Textarea, required=True)


class AddonStatusForm(ModelForm):
    class Meta:
        model = Addon
        fields = ('status', 'highest_status', 'outstanding')


class FileStatusForm(ModelForm):
    class Meta:
        model = File
        fields = ('status',)


FileFormSet = modelformset_factory(File, form=FileStatusForm,
                                   formset=BaseModelFormSet, extra=0)


class SiteEventForm(ModelForm):
    class Meta:
        model = SiteEvent
        fields = ('start', 'end', 'event_type', 'description',
                  'more_info_url')


class YesImSure(happyforms.Form):
    yes = forms.BooleanField(required=True, label="Yes, I'm sure")


class GenerateErrorForm(happyforms.Form):
    error = forms.ChoiceField(choices=(
                    ['zerodivisionerror', 'Zero Division Error (will email)'],
                    ['iorequesterror', 'IORequest Error (no email)'],
                    ['heka_statsd', 'Heka statsd message'],
                    ['heka_json', 'Heka JSON message'],
                    ['heka_cef', 'Heka CEF message'],
                    ['heka_sentry', 'Heka Sentry message'],
                    ['amo_cef', 'AMO CEF message'],
                    ))

    def explode(self):
        error = self.cleaned_data.get('error')

        if error == 'zerodivisionerror':
            1 / 0
        elif error == 'iorequesterror':
            class IOError(Exception):
                pass
            raise IOError('request data read error')
        elif error == 'heka_cef':
            environ = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_HOST': '127.0.0.1',
                            'PATH_INFO': '/', 'REQUEST_METHOD': 'GET',
                            'HTTP_USER_AGENT': 'MySuperBrowser'}

            config = {'cef.version': '0',
                           'cef.vendor': 'Mozilla',
                           'cef.device_version': '3',
                           'cef.product': 'zamboni',
                           'cef': True}

            settings.HEKA.cef('xx\nx|xx\rx', 5, environ, config,
                    username='me', ext1='ok=ok', ext2='ok\\ok',
                    logger_info='settings.HEKA')
        elif error == 'heka_statsd':
            settings.HEKA.incr(name=LOGGER_NAME)
        elif error == 'heka_json':
            settings.HEKA.heka(type="heka_json",
                    fields={'foo': 'bar', 'secret': 42,
                            'logger_type': 'settings.HEKA'})

        elif error == 'heka_sentry':
            # These are local variables only used
            # by Sentry's frame hacking magic.
            # They won't be referenced which may trigger flake8
            # errors.
            heka_conf = settings.HEKA_CONF  # NOQA
            active_heka_conf = settings.HEKA._config  # NOQA
            try:
                1 / 0
            except:
                settings.HEKA.raven('heka_sentry error triggered')
        elif error == 'amo_cef':
            from amo.utils import log_cef
            env = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_HOST': '127.0.0.1',
                            'PATH_INFO': '/', 'REQUEST_METHOD': 'GET',
                            'HTTP_USER_AGENT': 'MySuperBrowser'}
            log_cef(settings.STATSD_PREFIX, 6, env)


class PriceTiersForm(happyforms.Form):
    prices = forms.FileField()
