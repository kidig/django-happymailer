import json

from django import forms
from django.conf import settings
from django.conf.urls import url
from django.contrib import admin
from django.core.urlresolvers import reverse
from django.http import Http404, JsonResponse, HttpResponseBadRequest

from . import fake
from .backends.base import CompileError
from .models import TemplateModel
from .utils import layout_classes, template_classes, get_template, get_layout
from .mixins import TemplateImportExportMixin


class TemplateAdminForm(forms.ModelForm):
    subject = forms.CharField(widget=forms.Textarea(attrs={'class': 'vLargeTextField'}))
    layout = forms.ChoiceField(choices=[])

    def __init__(self, *args, **kwargs):
        super(TemplateAdminForm, self).__init__(*args, **kwargs)
        self.fields['layout'].choices = [(cls.name, cls.description or cls.name) for cls in layout_classes]

    class Meta:
        model = TemplateModel
        fields = ('layout', 'subject', 'body', 'enabled',)


class FakedataForm(forms.Form):
    layout = forms.CharField()
    template = forms.CharField()
    body = forms.CharField(required=False)
    subject = forms.CharField(required=False)
    variables = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super(FakedataForm, self).__init__(*args, **kwargs)
        self.fields['layout'].choices = [(cls.name, cls.name) for cls in layout_classes]
        self.fields['template'].choices = [(cls.name, cls.name) for cls in template_classes]


@admin.register(TemplateModel)
class TemplateAdmin(TemplateImportExportMixin, admin.ModelAdmin):
    list_display = ('name', 'enabled', 'version',)
    readonly_fields = ('name',)
    form = TemplateAdminForm
    fields = ('name', 'enabled', 'layout', 'subject', 'body',)

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        extras = [
            url(r'^preview/$', self.admin_site.admin_view(self.preview), name='%s_%s_preview' % info),
            url(r'^send_test/$', self.admin_site.admin_view(self.send_test), name='%s_%s_send_test' % info),
        ]
        return extras + super(TemplateAdmin, self).get_urls()

    def preview(self, request):
        form = FakedataForm(request.POST)
        if not form.is_valid():
            print(form.errors)
            return HttpResponseBadRequest()

        variables = json.loads(form.cleaned_data['variables'])
        template_cls = get_template(form.cleaned_data['template'])
        layout_cls = get_layout(form.cleaned_data['layout'])
        kwargs = fake.generate(template_cls.kwargs)

        tmpl = template_cls('spam', force_layout_cls=layout_cls, force_variables=variables, **kwargs)
        tmpl.body = form.cleaned_data['body']

        try:
            compiled = tmpl.compile()
        except CompileError:
            return HttpResponseBadRequest()

        return JsonResponse({
            'html': compiled,
        })

    def send_test(self, request):
        form = FakedataForm(request.POST)
        if not form.is_valid():
            print('errors:', form.errors)
            return HttpResponseBadRequest()

        variables = json.loads(form.cleaned_data['variables'])
        template_cls = get_template(form.cleaned_data['template'])
        layout_cls = get_layout(form.cleaned_data['layout'])
        kwargs = fake.generate(template_cls.kwargs)

        recipient = '{} <{}>'.format(request.user.get_full_name(), request.user.email)
        tmpl = template_cls(recipient, force_layout_cls=layout_cls, force_variables=variables, **kwargs)
        tmpl.body = form.cleaned_data['body']
        tmpl.subject = "Test: {}".format(form.cleaned_data['subject'])
        tmpl.send(force=True)

        return JsonResponse({
            'mail': recipient,
        })

    def get_actions(self, request):
        return None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):

        if object_id:
            obj = self.get_object(request, admin.utils.unquote(object_id))

            ModelForm = self.get_form(request, obj)
            if request.method == 'POST':
                form = ModelForm(request.POST, request.FILES, instance=obj)
                if form.is_valid():
                    new_object = form.save()
                    return JsonResponse(dict(status='ok'))
                else:
                    return JsonResponse(dict(status='error', errors=form.errors))

            template = None
            for cls in template_classes:
                if cls.name == obj.name:
                    template = cls

            if not template:
                raise Http404()

            variables = template.fake_variables()

            extra_context = dict(
                extra_context or {},
                happymailer_config=json.dumps({
                    'staticUrl': settings.STATIC_URL + 'happymailer/',
                    'template': {
                        'template': obj.name,
                        'body': obj.body or '',
                        'layout': obj.layout or layout_classes[0].name,
                        'enabled': obj.enabled,
                        'subject': obj.subject or '',
                    },
                    'changelistUrl': reverse('admin:happymailer_templatemodel_changelist'),
                    'changeUrl': reverse('admin:happymailer_templatemodel_change', args=[obj.pk]),
                    'previewUrl': reverse('admin:happymailer_templatemodel_preview'),
                    'sendtestUrl': reverse('admin:happymailer_templatemodel_send_test'),
                    'layouts': [{'value': cls.name, 'label': cls.description or cls.name}
                                for cls in layout_classes],
                    'variables': [{'name': x.name,
                                   'type': repr(x.trafaret),
                                   'value': variables.get(x.name, fake.generate(x.trafaret))}
                                  for x in template.variables.keys]
                }).replace('<', '\\u003C'),
            )

        return super(TemplateAdmin, self).changeform_view(request, object_id, form_url, extra_context)
