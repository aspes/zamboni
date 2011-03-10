import collections
import itertools
import json
import urlparse
import uuid

from django import http
from django.contrib import admin
from django.db import IntegrityError
from django.forms.models import modelformset_factory
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.csrf import csrf_exempt

import jingo

import amo
import amo.utils
import api.utils
import api.views
from amo.decorators import post_required
from amo.urlresolvers import reverse
from addons.decorators import addon_view_factory
from addons.models import Addon, AddonRecommendation
from browse.views import personas_listing
from bandwagon.models import Collection, SyncedCollection, CollectionToken
from reviews.models import Review
from stats.models import GlobalStat

from .models import DiscoveryModule
from .forms import DiscoveryModuleForm
from .modules import registry as module_registry

addon_view = addon_view_factory(Addon.objects.valid)


def pane(request, version, platform):

    def from_api(list_type):
        r = api_view(request, platform, version, list_type)
        return json.loads(r.content)
    try:
        qs = GlobalStat.objects.filter(name='addon_total_downloads')
        addon_downloads = qs.latest().count
    except GlobalStat.DoesNotExist:
        addon_downloads = None

    return jingo.render(request, 'discovery/pane.html',
                        {'modules': get_modules(request, platform, version),
                         'addon_downloads': addon_downloads,
                         'top_addons': from_api('hotness'),
                         'featured_addons': from_api('featured'),
                         'featured_personas': get_featured_personas(request),
                         'version': version, 'platform': platform})


def pane_account(request):
    return jingo.render(request, 'discovery/pane_account.html')


def get_modules(request, platform, version):
    lang = request.LANG
    qs = DiscoveryModule.objects.filter(app=request.APP.id)
    # Remove any modules without a registered backend or an ordering.
    modules = [m for m in qs if m.module in module_registry
                                and m.ordering is not None]
    # Remove modules that specify a locales string we're not part of.
    modules = [m for m in modules if not m.locales
                                     or lang in m.locales.split()]
    modules = sorted(modules, key=lambda x: x.ordering)
    return [module_registry[m.module](request, platform, version)
            for m in modules]


def get_featured_personas(request):
    categories, filter, base, category = personas_listing(request)
    featured = base & Addon.objects.featured(request.APP)
    return featured[:6]


def api_view(request, platform, version, list_type,
             api_version=1.5, format='json', mimetype='application/json'):
    """Wrapper for calling an API view."""
    view = api.views.ListView()
    view.request, view.version = request, api_version
    view.format, view.mimetype = format, mimetype
    return view.process_request(list_type, platform=platform, version=version)


@admin.site.admin_view
def module_admin(request):
    APP = request.APP
    # Custom sorting to drop ordering=NULL objects to the bottom.
    qs = DiscoveryModule.uncached.raw("""
        SELECT * from discovery_modules WHERE app_id = %s
        ORDER BY ordering IS NULL, ordering""", [APP.id])
    qs.ordered = True  # The formset looks for this.
    _sync_db_and_registry(qs, APP)

    Form = modelformset_factory(DiscoveryModule, form=DiscoveryModuleForm,
                                can_delete=True, extra=0)
    formset = Form(request.POST or None, queryset=qs)

    if request.method == 'POST' and formset.is_valid():
        formset.save()
        return redirect('discovery.module_admin')

    return jingo.render(request, 'discovery/module_admin.html',
                        {'formset': formset})


def _sync_db_and_registry(qs, app):
    """Match up the module registry and DiscoveryModule rows in the db."""
    existing = dict((m.module, m) for m in qs)
    add = [m for m in module_registry if m not in existing]
    delete = [m for m in existing if m not in module_registry]
    for m in add:
        DiscoveryModule.objects.create(module=m, app_id=app.id)
    for m in delete:
        DiscoveryModule.objects.get(module=m, app=app.id).delete()
    if add or delete:
        qs._result_cache = None


@csrf_exempt
@post_required
def recommendations(request, version, platform, limit=9):
    """
    Figure out recommended add-ons for an anonymous user based on POSTed guids.

    POST body looks like {"guids": [...]} with an optional "token" key if
    they've been here before.
    """
    try:
        POST = json.loads(request.raw_post_data)
        guids = POST['guids']
    except (ValueError, TypeError, KeyError):
        # Errors: invalid json, didn't get a dict, didn't find "guids".
        return http.HttpResponseBadRequest()

    addon_ids = get_addon_ids(guids)
    token = POST['token'] if 'token' in POST else get_random_token()

    if 'token' in POST:
        q = SyncedCollection.objects.filter(token_set__token=token)
        if q:
            # We've seen this user before.
            synced = q[0]
            if synced.addon_index == Collection.make_index(addon_ids):
                # Their add-ons didn't change, get out quick.
                recs = synced.get_recommendations()
                ids, recs = synced.get_recs(request.APP, version)
                return _recommendations(request, version, platform,
                                        limit, token, ids, recs)
            else:
                # Remove the link to the current sync, make a new one below.
                synced.token_set.filter(token=token).delete()

    synced = get_synced_collection(addon_ids, token)
    ids, recs = synced.get_recs(request.APP, version)
    return _recommendations(request, version, platform, limit,
                            token, ids, recs)


def _recommendations(request, version, platform, limit, token, ids, qs):
    """Return a JSON response for the recs view."""
    addons = api.views.addon_filter(qs, 'ALL', limit, request.APP,
                                    platform, version, shuffle=False)
    addons = dict((a.id, a) for a in addons)
    data = {'token': token,
            'addons': [api.utils.addon_to_dict(addons[i], disco=True)
                       for i in ids if i in addons]}
    content = json.dumps(data, cls=amo.utils.JSONEncoder)
    return http.HttpResponse(content, content_type='application/json')


def get_addon_ids(guids):
    return Addon.objects.filter(guid__in=guids).values_list('id', flat=True)


def get_synced_collection(addon_ids, token):
    """
    Get a synced collection for these addons. May reuse an existing collection.

    The token is associated with the collection.
    """
    index = Collection.make_index(addon_ids)
    try:
        c = (SyncedCollection.objects.no_cache()
             .filter(addon_index=index))[0]
    except IndexError:
        c = SyncedCollection.objects.create(listed=False)
        c.set_addons(addon_ids)

    # Don't fail if the token is already there. This shouldn't happen but it
    # seems to be pretty common.
    try:
        c.token_set.create(token=token)
    except IntegrityError:
        pass
    return c


def get_random_token():
    """Get a random token for the user, make sure it's unique."""
    while 1:
        token = unicode(uuid.uuid4())
        if CollectionToken.objects.filter(token=token).count() == 0:
            return token


@addon_view
def addon_detail(request, addon):
    reviews = Review.objects.latest().filter(addon=addon)
    src = request.GET.get('src', 'discovery-details')
    return jingo.render(request, 'discovery/addons/detail.html',
                        {'addon': addon, 'reviews': reviews,
                         'get_replies': Review.get_replies, 'src': src})


@addon_view
def addon_eula(request, addon, file_id):
    if not addon.eula:
        return http.HttpResponseRedirect(addon.get_url_path())
    if file_id is not None:
        version = get_object_or_404(addon.versions, files__id=file_id)
    else:
        version = addon.current_version
    src = request.GET.get('src', 'discovery-details')
    return jingo.render(request, 'discovery/addons/eula.html',
                        {'addon': addon, 'version': version, 'src': src})


def recs_transform(recs):
    ids = [r.addon_id for r in recs] + [r.other_addon_id for r in recs]
    addons = dict((a.id, a) for a in Addon.objects.filter(id__in=ids))
    for r in recs:
        r.addon = addons[r.addon_id]
        r.other_addon = addons[r.other_addon_id]


@admin.site.admin_view
def recs_debug(request):
    if request.method == 'POST':
        url = request.POST.get('url')
        if url:
            fragment = urlparse.urlparse(url).fragment
            guids = json.loads(urlparse.unquote(fragment)).keys()
            qs = ','.join(map(str, get_addon_ids(guids)))
            return redirect(reverse('discovery.recs.debug') + '?ids=' + qs)

    ctx = {'ids': request.GET.get('ids')}
    if 'ids' in request.GET:
        ids = map(int, request.GET['ids'].split(','))
        ctx['addons'] = Addon.objects.filter(id__in=ids)
        synced = get_synced_collection(ids, None)
        recs = synced.get_recommendations()
        ctx['recommended'] = recs.addons.order_by('collectionaddon__ordering')

        recs = AddonRecommendation.objects.filter(addon__in=ids)
        recs_transform(recs)
        ctx['recs'] = dict((k, list(v)) for k, v in
                           itertools.groupby(recs, key=lambda x: x.addon_id))

        all_recs = collections.defaultdict(int)
        for rec in recs:
            all_recs[rec.other_addon] += rec.score
        ctx['all_recs'] = sorted([(v, k) for k, v in all_recs.items()],
                                 reverse=True)

        fragment = dict((a.guid, {'type': 'extension'}) for a in ctx['addons']
                        if a.type == amo.ADDON_EXTENSION)
        ctx['fragment'] = json.dumps(fragment, separators=(',', ':'))

    return jingo.render(request, 'discovery/recs-debug.html', ctx)
