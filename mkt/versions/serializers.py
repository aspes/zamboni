from rest_framework import serializers

from versions.models import Version
from mkt.features.serializers import AppFeaturesSerializer


class SimpleVersionSerializer(serializers.ModelSerializer):
    resource_uri = serializers.HyperlinkedIdentityField(
        view_name='version-detail')

    class Meta:
        model = Version
        fields = ('version', 'resource_uri')


class VersionSerializer(serializers.ModelSerializer):
    addon = serializers.HyperlinkedRelatedField(view_name='app-detail',
                                                read_only=True)

    class Meta:
        model = Version
        fields = ('id', 'addon', '_developer_name', 'releasenotes', 'version')
        depth = 0
        field_rename = {
            '_developer_name': 'developer_name',
            'releasenotes': 'release_notes',
            'addon': 'app'
        }

    def to_native(self, obj):
        native = super(VersionSerializer, self).to_native(obj)

        # Add non-field data to the response.
        native.update({
            'features': AppFeaturesSerializer().to_native(obj.features),
            'is_current_version': obj.addon.current_version == obj,
            'releasenotes': (unicode(obj.releasenotes) if obj.releasenotes else
                             None),
        })

        # Remap fields to friendlier, more backwards-compatible names.
        for old, new in self.Meta.field_rename.items():
            native[new] = native[old]
            del native[old]

        return native
