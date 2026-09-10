from django.urls import path

from . import views

app_name = "finance"
urlpatterns = [
    path("", views.overview, name="overview"),
    path("comptes/nouveau/", views.account_create, name="account_create"),
    path("periodes/nouvelle/", views.period_create, name="period_create"),
    path("registre/<str:section>/", views.workspace, name="workspace"),
    path("creer/<str:kind>/", views.create_record, name="create_record"),
    path("journal/nouveau/", views.batch_create, name="batch_create"),
    path("journal/<int:pk>/", views.batch_detail, name="batch"),
    path("journal/<int:pk>/<str:action>/", views.batch_action, name="batch_action"),
    path("periodes/<int:pk>/", views.period_detail, name="period"),
    path("periodes/<int:pk>/export.csv", views.report_csv, name="report_csv"),
    path("paiements/<str:kind>/<int:pk>/", views.payment, name="payment"),
    path("distributions/<int:pk>/proposition/", views.distribution_proposal, name="distribution_proposal"),
    path("distributions/<int:pk>/approbation/", views.distribution_approve, name="distribution_approve"),
    path("parties/<int:pk>/", views.partner_detail, name="partner"),
    path("imports/<int:pk>/revue/", views.import_review, name="import_review"),
    path("apports/nouveau/", views.contribution_create, name="contribution_create"),
    path("transferts/nouveau/", views.transfer_create, name="transfer_create"),
    path("transferts/<int:pk>/<str:action>/", views.transfer_action, name="transfer_action"),
    path("garanties/", views.guarantee_form, name="guarantee_form"),
    path("parties/nouvelle/", views.party_onboarding, name="party_onboarding"),
    path("remunerations/conditions/", views.remuneration_terms, name="remuneration_terms"),
    path("caisses-agent/nouvelle/", views.agent_cash_open, name="agent_cash_open"),
    path("conversions/nouvelle/", views.conversion_create, name="conversion_create"),
    path("politiques/nouvelle/", views.policy_create, name="policy_create"),
    path("demandes/<int:pk>/", views.request_decide, name="request_decide"),
    path("mon-espace/", views.party_space, name="party_space"),
    path("mon-espace/commissions/", views.commission_choice, name="commission_choice"),
]
