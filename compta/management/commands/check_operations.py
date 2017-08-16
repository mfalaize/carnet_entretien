import calendar
import decimal
from datetime import date

from django.conf import settings
from django.core.mail import send_mail
from django.core.management import BaseCommand
from django.template.loader import get_template

from compta.bank import get_bank_class
from compta.models import Compte, Epargne, OperationEpargne, Budget


def generate_mail(compte):
    # Dernier jour du mois, on envoie un mail pour les comptes joints afin de fournir les sommes à y déposer
    if date.today().day == calendar.monthrange(date.today().year, date.today().month)[1]:
        if compte.utilisateurs.count() > 1:
            budgets = Budget.objects.filter(compte_associe=compte)
            total_budget = 0
            for budget in budgets:
                total_budget += budget.budget
            if total_budget > 0:
                utilisateurs = compte.utilisateurs.all()
                total_salaire = 0
                total_part = 0
                total_a_verser = 0
                for utilisateur in utilisateurs:
                    total_salaire += utilisateur.revenus_personnels
                if total_salaire > 0:
                    for utilisateur in utilisateurs:
                        utilisateur.part = int(utilisateur.revenus_personnels / total_salaire * 100)
                        utilisateur.a_verser = int(utilisateur.revenus_personnels / total_salaire * total_budget) - int(compte.solde)
                        total_part += utilisateur.part
                        total_a_verser += utilisateur.a_verser

                    html_content = get_template('compta/mail/partage_compte_joint.html').render(locals())

                    mails = []
                    for user in utilisateurs:
                        if user.email is not None:
                            mails.append(user.email)
                    if len(mails) > 0:
                        send_mail(
                            '[Homelab] Sommes à verser sur {}'.format(str(compte)),
                            "",
                            settings.DEFAULT_FROM_EMAIL, mails, html_message=html_content)


def check_operations():
    """Récupère les dernières opérations bancaires en ligne, inscrit les nouvelles en base et les envoie par mail"""
    comptes = Compte.objects.all()
    for compte in comptes:
        epargnes = Epargne.objects.filter(utilisateurs__in=compte.utilisateurs.all()).distinct()
        operations = compte.operation_set.all()
        bank_class = get_bank_class(compte.identifiant.banque)
        has_changed = False

        with bank_class(compte.identifiant.login, compte.identifiant.mot_de_passe, compte.numero_compte) as bank:
            new_operations = bank.fetch_last_operations()
            new_solde = bank.fetch_balance()

        for new_operation in new_operations:
            found = False
            for operation in operations:
                if operation.date_operation == new_operation.date_operation and operation.libelle == new_operation.libelle:
                    found = True
                    break
            if not found:
                new_operation.compte = compte
                new_operation.save()

                if compte.epargne:
                    if new_operation.montant >= 0:
                        for epargne in epargnes:
                            new_operation.hors_budget = True
                            new_operation.save()

                            op = OperationEpargne()
                            op.epargne = epargne
                            op.montant = decimal.Decimal(new_operation.montant * epargne.pourcentage_alloue / 100)
                            op.operation = new_operation
                            op.save()

                            epargne.solde += op.montant
                            epargne.save()
                    else:
                        op = OperationEpargne()
                        op.montant = new_operation.montant
                        op.operation = new_operation
                        op.save()
                        has_changed = True
                else:
                    has_changed = True

        if compte.solde != new_solde:
            compte.solde = new_solde
            compte.save()

        if has_changed:
            mails = []
            for user in compte.utilisateurs.all():
                if user.email is not None:
                    mails.append(user.email)
            if len(mails) > 0:
                send_mail(
                    '[Homelab] De nouvelles opérations sont à catégoriser sur {}'.format(str(compte)),
                    "",
                    settings.DEFAULT_FROM_EMAIL, mails)

        generate_mail(compte)


class Command(BaseCommand):
    help = "Déclenche le script qui vérifie les nouvelles opérations bancaires et qui envoie des mails lorsqu'il y en a des nouvelles"

    def handle(self, *args, **options):
        check_operations()
