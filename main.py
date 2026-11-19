import logging
from typing import Iterator, List, Dict

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

from abstract_customer import get_abccustomer_by_lead_login, create_or_update_customer, \
    get_abccustomer_by_amocustomer_login, dict_compare_merge, create_or_update_lead
from amocrm import Customer, COMPANIES, CONTACTS, Lead, amocrm_get
from models import db, AbstractCustomer

logger = logging.getLogger(__name__)


def lead_action(lead: Lead, leads_by_id, records_by_customer_login, customers_by_id, collected_contacts, collected_companies):
    abstract_customer = leads_by_id.get(lead.id)
    lead_login = lead.get_field('Логин')
    if lead_login:
        abs_customer_login = records_by_customer_login.get(lead_login)
        if abstract_customer is None:
            abstract_customer = abs_customer_login
        if abs_customer_login is not None:
            if abs_customer_login is not abstract_customer:
                abstract_customer.amo_customers = dict_compare_merge(abstract_customer.amo_customers or [],
                                                                     abs_customer_login.amo_customers or [])
                abstract_customer.amo_leads = dict_compare_merge(abstract_customer.amo_leads or [],
                                                                 abs_customer_login.amo_leads or [])
                abstract_customer.amo_contacts = dict_compare_merge(abstract_customer.amo_contacts or [],
                                                                    abs_customer_login.amo_contacts or [])
                abstract_customer.amo_companies = dict_compare_merge(abstract_customer.amo_companies or [],
                                                                     abs_customer_login.amo_companies or [])
                flag_modified(abstract_customer, "amo_customers")
                flag_modified(abstract_customer, "amo_leads")
                flag_modified(abstract_customer, "amo_contacts")
                flag_modified(abstract_customer, "amo_companies")

                for ids, record in leads_by_id.items():
                    if record is abs_customer_login:
                        leads_by_id[ids] = abstract_customer

                for ids, record in customers_by_id.items():
                    if record is abs_customer_login:
                        customers_by_id[ids] = abstract_customer

                records_by_customer_login.clear()

                for record in set(customers_by_id.values()):
                    for item in record.amo_customers or []:
                        login = item["general"].get("login")
                        if not login:
                            continue

                        if login not in records_by_customer_login:
                            records_by_customer_login[login] = record
                        elif records_by_customer_login[login] is not record:
                            records_by_customer_login[login] = None

                if abstract_customer not in collected_contacts:
                    collected_contacts[abstract_customer] = set()

                collected_contacts[abstract_customer].update(
                    collected_contacts.pop(abs_customer_login, set())
                )

                if abstract_customer not in collected_companies:
                    collected_companies[abstract_customer] = set()

                collected_companies[abstract_customer].update(
                    collected_companies.pop(abs_customer_login, set())
                )


                db.session.delete(abs_customer_login)
    return create_or_update_lead(abstract_customer, lead)


def collect_leads_customers(is_full=True):
    """
     Берем всех покупателей, если его нет в бд - делаем новую запись AbstractCutomer, в него кладем.
     Есть есть - находим его запись по id и перезаписываем

     Вот так у нас коллектятся все кастомеры

     Далее берутся все лиды. Ищутся их записи в бд по id, если не находится и есть логин
     - находим его запись по кастомеру с тем же логином, кладем к нему.
     Если нет логина/нет кастомера - пропускаем. Если нашелся - перезаписываем соответственно
         abstract_id_customer : {
        customers: [...],
        leads: [...],
        contacts: [...],
        companies: [...]
    }
    """
    session = db.session
    try:
        customers: Iterator[Customer] = amocrm_get(Customer, _with=f"{COMPANIES},{CONTACTS}")
        logger.info(f"Collected customers {is_full=}")

        existing_records = session.query(AbstractCustomer).all()
        logger.info(f"full customers")

        customers_by_id = {}
        collected_contacts = {}
        collected_companies = {}
        records_by_lead_login = {}

        for record in existing_records:
            for item in record.amo_customers or []:
                customers_by_id[item["general"]["id"]] = record

            for item in record.amo_leads or []:
                login = item["general"].get("login")
                if not login:
                    continue

                if login not in records_by_lead_login:
                    records_by_lead_login[login] = record
                elif records_by_lead_login[login] is not record:
                    records_by_lead_login[login] = None

        collected_customers = set()

        for customer in customers:
            if is_full is False and customer.status_id == 111349:
                continue
            customer_login = customer.get_field('Логин')
            if not customer_login:
                continue
            abstract_customer = customers_by_id.get(customer.id)

            if not abstract_customer:
                abstract_customer: AbstractCustomer | None = records_by_lead_login.get(customer_login)

            abstract_customer = create_or_update_customer(
                abstract_customer, customer
            )
            customers_by_id[customer.id] = abstract_customer

            if abstract_customer not in collected_contacts:
                collected_contacts[abstract_customer] = set()

            for contact in customer.contacts or []:
                collected_contacts[abstract_customer].add(contact["id"])

            if abstract_customer not in collected_companies:
                collected_companies[abstract_customer] = set()

            for company in customer.companies or []:
                collected_companies[abstract_customer].add(company["id"])

            if is_full is True: collected_customers.add(customer.id)

        session.flush()

        leads_by_id = {}

        collected_leads = set()

        for record in existing_records:
            for item in record.amo_leads or []:
                lead_id = item["general"]["id"]
                leads_by_id[lead_id] = record

        records_by_customer_login = {}

        for record in set(customers_by_id.values()):
            for item in record.amo_customers or []:
                login = item["general"].get("login")
                if not login:
                    continue

                if login not in records_by_customer_login:
                    records_by_customer_login[login] = record
                elif records_by_customer_login[login] is not record:
                    records_by_customer_login[login] = None

        amo_leads: Iterator[Lead] = amocrm_get(Lead, _with=f"{CONTACTS}")
        logger.info(f"Collected leads {is_full=}")
        for amo_lead in amo_leads:
            if is_full is False and amo_lead.status_id == 143:
                continue
            try:
                abc_customer = lead_action(amo_lead, leads_by_id, records_by_customer_login, customers_by_id, collected_contacts, collected_companies)
                if abc_customer is not None:
                    leads_by_id[amo_lead.id] = abc_customer
                    if abc_customer not in collected_contacts:
                        collected_contacts[abc_customer] = set()

                    for contact in amo_lead.contacts or []:
                        collected_contacts[abc_customer].add(contact["id"])

                if is_full is True:
                    collected_leads.add(amo_lead.id)
            except Exception as e:
                logger.exception(f'{amo_lead} {e}')
                raise

        if is_full is False:
            session.commit()
            return

        logger.info("Чистка покупателей общая")

        for customer_del_id in set(customers_by_id.values()):
            record = customer_del_id
            record.amo_customers = [
                item for item in record.amo_customers or []
                if item["general"]["id"] in collected_customers
                                 and "удалить" not in (item["general"].get("name") or "").lower()
            ]
            record.amo_contacts = [
                item for item in record.amo_contacts or []
                if item["general"]["id"] in collected_contacts.get(record, set())
                                 and "удалить" not in (item["general"].get("name") or "").lower()
            ]
            record.amo_companies = [
                item for item in record.amo_companies or []
                if item["general"]["id"] in collected_companies.get(record, set())
                                 and "удалить" not in (item["general"].get("name") or "").lower()
            ]

        for lead_del_id in set(leads_by_id.values()):
            record = lead_del_id

            record.amo_leads = [
                item for item in record.amo_leads or []
                if item["general"]["id"] in collected_leads
                                 and "удалить" not in (item["general"].get("name") or "").lower()
            ]
            record.amo_contacts = [
                item for item in record.amo_contacts or []
                if item["general"]["id"] in collected_contacts.get(record, set())
                                 and "удалить" not in (item["general"].get("name") or "").lower()
            ]
            record.amo_companies = [
                item for item in record.amo_companies or []
                if item["general"]["id"] in collected_companies.get(record, set())
                                 and "удалить" not in (item["general"].get("name") or "").lower()
            ]

        for record in session.query(AbstractCustomer).all():
            if not record.amo_customers and not record.amo_leads:
                session.delete(record)

        session.commit()
    except Exception as e:
        logger.exception(f"Чистка: {e}")
        session.rollback()
        raise


def filter_merge_abstract_customer(abc_customer: AbstractCustomer, attr: str,
                                   check_ids: List[int]):
    entity = abc_customer.__getattribute__(attr)
    for l in entity:
        entity = [c for c in entity if
                  'удалить' not in l['general'].get('name').lower() and l['general'].get('id') in check_ids]
        setattr(abc_customer, attr, entity)
        flag_modified(abc_customer, attr)
        db.session.commit()


def filter_abc_customer(abc_customer: AbstractCustomer, ids_map: Dict):
    if abc_customer.amo_leads:
        try:
            filter_merge_abstract_customer(abc_customer=abc_customer,
                                           attr="amo_leads",
                                           check_ids=ids_map[abc_customer.id]["leads"])
        except Exception as e:
            logger.exception(e)
    if abc_customer.amo_customers:
        try:
            filter_merge_abstract_customer(abc_customer=abc_customer,
                                           attr="amo_customers",
                                           check_ids=ids_map[abc_customer.id]["customers"])
        except Exception as e:
            logger.exception(e)
    if abc_customer.amo_contacts:
        try:
            filter_merge_abstract_customer(abc_customer=abc_customer,
                                           attr="amo_contacts",
                                           check_ids=ids_map[abc_customer.id]["contacts"])
        except Exception as e:
            logger.exception(e)
    if abc_customer.amo_companies:
        try:
            filter_merge_abstract_customer(abc_customer=abc_customer,
                                           attr="amo_companies",
                                           check_ids=ids_map[abc_customer.id]["companies"])
        except Exception as e:
            logger.exception(e)
    if not abc_customer.amo_leads and not abc_customer.amo_customers:
        db.session.flush()
        db.session.delete(abc_customer)
    db.session.commit()
