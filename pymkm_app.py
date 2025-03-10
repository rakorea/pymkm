#!/usr/bin/env python3
"""
The PyMKM example app.
"""

__author__ = "Andreas Ehrlund"
__version__ = "1.2.1"
__license__ = "MIT"

import csv
import json
import logging
import math
import os.path
import pprint
import sys

import progressbar
import tabulate as tb
import requests

from pymkm_helper import PyMkmHelper
from pymkmapi import PyMkmApi, api_wrapper, NoResultsError
from micro_menu import *

ALLOW_REPORTING = True


class PyMkmApp:
    logging.basicConfig(stream=sys.stderr, level=logging.WARN)

    def __init__(self, config=None):
        if (config == None):
            logging.debug(">> Loading config file")
            try:
                self.config = json.load(open('config.json'))
            except FileNotFoundError:
                logging.error(
                    "You must copy config_template.json to config.json and populate the fields.")
                sys.exit(0)
        else:
            self.config = config

        self.api = PyMkmApi(config=self.config)

    def report(self, command):
        if ALLOW_REPORTING:
            try:
                r = requests.post('https://andli-stats-server.herokuapp.com/pymkm',
                                  json={"command": command, "version": __version__})
            except Exception as err:
                pass

    def start(self):
        menu = MicroMenu(f"PyMKM {__version__}")

        menu.add_function_item("Update stock prices",
                               self.update_stock_prices_to_trend, {
                                   'api': self.api}
                               )
        menu.add_function_item("Update price for a card",
                               self.update_product_to_trend, {'api': self.api}
                               )
        menu.add_function_item("List competition for a card",
                               self.list_competition_for_product, {
                                   'api': self.api}
                               )
        menu.add_function_item("Find deals from a user",
                               self.find_deals_from_user, {
                                   'api': self.api}
                               )
        menu.add_function_item("Show top 20 expensive items in stock",
                               self.show_top_expensive_articles_in_stock, {
                                   'num_articles': 20, 'api': self.api}
                               )
        menu.add_function_item("Show account info",
                               self.show_account_info, {'api': self.api}
                               )
        menu.add_function_item("Clear entire stock (WARNING)",
                               self.clear_entire_stock, {'api': self.api}
                               )
        menu.add_function_item("Import stock from .\list.csv",
                               self.import_from_csv, {'api': self.api}
                               )

        menu.show()

    @api_wrapper
    def update_stock_prices_to_trend(self, api):
        ''' This function updates all prices in the user's stock to TREND. '''
        self.report("update stock price to trend")

        undercut_local_market = PyMkmHelper.prompt_bool(
            'Try to undercut local market? (slower, more requests)')

        uploadable_json = self.calculate_new_prices_for_stock(
            undercut_local_market, api=self.api)

        if len(uploadable_json) > 0:

            self.display_price_changes_table(uploadable_json)

            if PyMkmHelper.prompt_bool("Do you want to update these prices?") == True:
                # Update articles on MKM
                api.set_stock(uploadable_json)
                print('Prices updated.')
            else:
                print('Prices not updated.')
        else:
            print('No prices to update.')

    @api_wrapper
    def update_product_to_trend(self, api):
        ''' This function updates one product in the user's stock to TREND. '''
        self.report("update product price to trend")

        search_string = PyMkmHelper.prompt_string('Search card name')
        undercut_local_market = PyMkmHelper.prompt_bool(
            'Try to undercut local market? (slower, more requests)')

        try:
            articles = api.find_stock_article(search_string, 1)
        except Exception as err:
            print(err)

        if len(articles) > 1:
            article = self.select_from_list_of_articles(articles)
        else:
            article = articles[0]
            print('Found: {} [{}].'.format(article['product']
                                           ['enName'], article['product']['expansion']))
        r = self.get_article_with_updated_price(
            article, undercut_local_market, api=self.api)

        if r:
            self.draw_price_changes_table([r])

            print('\nTotal price difference: {}.'.format(
                str(round(sum(item['price_diff'] * item['count']
                              for item in [r]), 2))
            ))

            if PyMkmHelper.prompt_bool("Do you want to update these prices?") == True:
                # Update articles on MKM
                api.set_stock([r])
                print('Price updated.')
            else:
                print('Prices not updated.')
        else:
            print('No prices to update.')

    @api_wrapper
    def list_competition_for_product(self, api):
        self.report("list competition for product")

        search_string = PyMkmHelper.prompt_string('Search card name')
        is_foil = PyMkmHelper.prompt_bool("Foil?")

        result = api.find_product(search_string, **{
            # 'exact ': 'true',
            'idGame': 1,
            'idLanguage': 1,
            # TODO: Add Partial Content support
            # TODO: Add language support
        })

        if (result):
            products = result['product']

            stock_list_products = [x['idProduct']
                                   for x in self.get_stock_as_array(api=self.api)]
            products = [x for x in products if x['idProduct']
                        in stock_list_products]

            if len(products) == 0:
                print('No matching cards in stock.')
            else:
                if len(products) > 1:
                    product = self.select_from_list_of_products(
                        [i for i in products if i['categoryName'] == 'Magic Single'])
                elif len(products) == 1:
                    product = products[0]

                self.show_competition_for_product(
                    product['idProduct'], product['enName'], is_foil, api=self.api)
        else:
            print('No results found.')

    @api_wrapper
    def find_deals_from_user(self, api):
        self.report("find deals from user")

        search_string = PyMkmHelper.prompt_string('Enter username')

        try:
            result = api.find_user_articles(search_string)
        except NoResultsError as err:
            print(err.mkm_msg())
        else:

            if (result):
                filtered_articles = [x for x in result if x.get(
                    'condition') in PyMkmApi.conditions[:3]]  # EX+
                sorted_articles = sorted(
                    result, key=lambda x: x['price'], reverse=True)
                print(
                    f"User '{search_string}' has {len(sorted_articles)} articles in stock.")
                num_searches = int(PyMkmHelper.prompt_string(
                    f'Searching top X expensive cards (EX+) for deals, choose X (1-{len(sorted_articles)})'))
                if num_searches > 1 and num_searches <= len(sorted_articles):
                    table_data = []

                    index = 0
                    bar = progressbar.ProgressBar(max_value=num_searches)
                    for article in sorted_articles[:num_searches]:
                        p = api.get_product(article['idProduct'])
                        name = p['product']['enName']
                        expansion = p['product']['expansion']['enName']
                        condition = article.get('condition')
                        language = article['language']['languageName']
                        foil = article['isFoil']
                        price = float(article['price'])
                        if foil:
                            market_price = p['product']['priceGuide']['TRENDFOIL']
                        else:
                            market_price = p['product']['priceGuide']['TREND']
                        price_diff = price - market_price
                        if price_diff < 0:
                            table_data.append([
                                name,
                                expansion,
                                condition,
                                language,
                                u'\u2713' if foil else '',
                                price,
                                market_price,
                                price_diff
                            ])
                        index += 1
                        bar.update(index)
                    bar.finish()

                    if table_data:
                        print('Found some interesting prices:')
                        print(tb.tabulate(sorted(table_data, key=lambda x: x[5], reverse=True),
                                          headers=['Name', 'Expansion', 'Condition', 'Language', 'Foil?',
                                                   'Price', 'Market price', 'Market diff'],
                                          tablefmt="simple")
                              )
                    else:
                        print('Found no deals. :(')
                else:
                    print("Invalid number.")
            else:
                print('No results found.')

    @api_wrapper
    def show_top_expensive_articles_in_stock(self, num_articles, api):
        self.report("show top expensive in stock")

        stock_list = self.get_stock_as_array(api=self.api)
        table_data = []
        total_price = 0

        for article in stock_list:
            name = article['product']['enName']
            expansion = article.get('product').get('expansion')
            foil = article.get('isFoil')
            language_code = article.get('language')
            language_name = language_code.get('languageName')
            price = article.get('price')
            table_data.append(
                [name, expansion, u'\u2713' if foil else '', language_name if language_code != 1 else '', price])
            total_price += price
        if len(stock_list) > 0:
            print('Top {} most expensive articles in stock:\n'.format(
                str(num_articles)))
            print(tb.tabulate(sorted(table_data, key=lambda x: x[4], reverse=True)[:num_articles],
                              headers=['Name', 'Expansion',
                                       'Foil?', 'Language', 'Price'],
                              tablefmt="simple")
                  )
            print('\nTotal stock value: {}'.format(str(total_price)))
        return None

    @api_wrapper
    def show_account_info(self, api):
        self.report("show account info")

        pp = pprint.PrettyPrinter()
        pp.pprint(api.get_account())

    @api_wrapper
    def clear_entire_stock(self, api):
        self.report("clear entire stock")

        stock_list = self.get_stock_as_array(api=self.api)
        if PyMkmHelper.prompt_bool("Do you REALLY want to clear your entire stock ({} items)?".format(len(stock_list))) == True:

            # for article in stock_list:
                # article['count'] = 0
            delete_list = [{'count': x['count'], 'idArticle': x['idArticle']}
                           for x in stock_list]

            api.delete_stock(delete_list)
            print('Stock cleared.')
        else:
            print('Aborted.')

    @api_wrapper
    def import_from_csv(self, api):
        self.report("import from csv")

        print("Note the required format: Card, Set name, Quantity, Foil, Language (with header row).")
        print("Cards are added in condition NM.")
        problem_cards = []
        with open('list.csv', newline='') as csvfile:
            csv_reader = csvfile.readlines()
            index = 0
            bar = progressbar.ProgressBar(
                max_value=(sum(1 for row in csv_reader)) - 1)
            csvfile.seek(0)
            for row in csv_reader:
                if index > 0:
                    (name, set_name, count, foil, language, *other) = row.split(',')
                    if (all(v is not '' for v in [name, set_name, count])):
                        possible_products = api.find_product(name)['product']
                        product_match = [x for x in possible_products if x['expansionName']
                                         == set_name and x['categoryName'] == "Magic Single"]
                        if len(product_match) == 0:
                            problem_cards.append(row)
                        elif len(product_match) == 1:
                            foil = (True if foil == 'Foil' else False)
                            language_id = (
                                1 if language == '' else api.languages.index(language) + 1)
                            price = self.get_price_for_product(
                                product_match[0]['idProduct'], foil, language_id=language_id, api=self.api)
                            card = {
                                'idProduct': product_match[0]['idProduct'],
                                'idLanguage': language_id,
                                'count': count,
                                'price': str(price),
                                'condition': 'NM',
                                'isFoil': ('true' if foil else 'false')
                            }
                            api.add_stock([card])
                        else:
                            problem_cards.append(row)

                bar.update(index)
                index += 1
            bar.finish()
        if len(problem_cards) > 0:
            try:
                with open('failed_imports.csv', 'w', newline='', encoding='utf-8') as csvfile:
                    csv_writer = csv.writer(csvfile)
                    csv_writer.writerows(problem_cards)
                print('Wrote failed imports to failed_imports.csv')
                print(
                    'Most failures are due to mismatching set names or multiple versions of cards.')
            except Exception as err:
                print(err.value)

# End of menu item functions ============================================

    def select_from_list_of_products(self, products):
        index = 1
        for product in products:
            print('{}: {} [{}] {}'.format(index, product['enName'],
                                          product['expansionName'], product['rarity']))
            index += 1
        choice = int(input("Choose card: "))
        return products[choice - 1]

    def select_from_list_of_articles(self, articles):
        index = 1
        for article in articles:
            product = article['product']
            print('{}: {} [{}] {}'.format(index, product['enName'],
                                          product['expansion'], product['rarity']))
            index += 1
        choice = int(input("Choose card: "))
        return articles[choice - 1]

    def show_competition_for_product(self, product_id, product_name, is_foil, api):
        print("Found product: {}".format(product_name))
        table_data_local, table_data = self.get_competition(
            api, product_id, is_foil)
        if table_data_local:
            self.print_product_top_list(
                "Local competition:", table_data_local, 4, 20)
        if table_data:
            self.print_product_top_list("Top 20 cheapest:", table_data, 4, 20)
        else:
            print('No prices found.')

    def get_competition(self, api, product_id, is_foil):
        account = api.get_account()['account']
        country_code = account['country']
        articles = api.get_articles(product_id, **{
            'isFoil': str(is_foil).lower(),
            'isAltered': 'false',
            'isSigned': 'false',
            'minCondition': 'EX',
            'country': country_code,
            'idLanguage': 1
        })
        table_data = []
        table_data_local = []
        for article in articles:
            username = article['seller']['username']
            if article['seller']['username'] == account['username']:
                username = '-> ' + username
            item = [
                username,
                article['seller']['address']['country'],
                article['condition'],
                article['count'],
                article['price']
            ]
            if article['seller']['address']['country'] == country_code:
                table_data_local.append(item)
            table_data.append(item)
        return table_data_local, table_data

    def print_product_top_list(self, title_string, table_data, sort_column, rows):
        print(70*'-')
        print('{} \n'.format(title_string))
        print(tb.tabulate(sorted(table_data, key=lambda x: x[sort_column], reverse=False)[:rows],
                          headers=['Username', 'Country',
                                   'Condition', 'Count', 'Price'],
                          tablefmt="simple"))
        print(70*'-')
        print('Total average price: {}, Total median price: {}, Total # of articles: {}\n'.format(
            str(PyMkmHelper.calculate_average(table_data, 3, 4)),
            str(PyMkmHelper.calculate_median(table_data, 3, 4)),
            str(len(table_data))
        )
        )

    def calculate_new_prices_for_stock(self, undercut_local_market, api):
        stock_list = self.get_stock_as_array(api=self.api)
        # HACK: filter out a foil product
        # stock_list = [x for x in stock_list if x['isFoil']]

        result_json = []
        total_price = 0
        index = 0

        bar = progressbar.ProgressBar(max_value=len(stock_list))
        for article in stock_list:
            updated_article = self.get_article_with_updated_price(
                article, undercut_local_market, api=self.api)
            if updated_article:
                result_json.append(updated_article)
                total_price += updated_article.get('price')
            else:
                total_price += article.get('price')
            index += 1
            bar.update(index)
        bar.finish()

        print('Total stock value: {}'.format(str(round(total_price, 2))))
        return result_json

    def get_article_with_updated_price(self, article, undercut_local_market=False, api=None):
        # TODO: compare prices also for signed cards, like foils
        if not article.get('isSigned'):  # keep prices for signed cards fixed
            new_price = self.get_price_for_product(
                article['idProduct'],
                article['product']['rarity'],
                article['isFoil'],
                language_id=article['language']['idLanguage'],
                undercut_local_market=undercut_local_market,
                api=self.api)
            price_diff = new_price - article['price']
            if price_diff != 0:
                return {
                    "name": article['product']['enName'],
                    "foil": article['isFoil'],
                    "old_price": article['price'],
                    "price": new_price,
                    "price_diff": price_diff,
                    "idArticle": article['idArticle'],
                    "count": article['count']
                }

    def get_rounding_limit_for_rarity(self, rarity):
        rounding_limit = self.config['price_limit_by_rarity']['default']
        try:
            rounding_limit = float(
                self.config['price_limit_by_rarity'][rarity.lower()])
        except KeyError as err:
            print(f"ERROR: Unknown rarity '{rarity}'. Using default rounding.")
        return rounding_limit

    def get_price_for_product(self, product_id, rarity, is_foil, language_id=1, undercut_local_market=False, api=None):
        r = api.get_product(product_id)
        rounding_limit = self.get_rounding_limit_for_rarity(rarity)

        if not is_foil:
            trend_price = r['product']['priceGuide']['TREND']
        else:
            trend_price = r['product']['priceGuide']['TRENDFOIL']

        # Set competitive price for region
        if undercut_local_market:
            table_data_local, table_data = self.get_competition(
                api, product_id, is_foil)

            if len(table_data_local) > 0:
                # Undercut if there is local competition
                lowest_in_country = PyMkmHelper.round_down_to_limit(rounding_limit,
                                                                    PyMkmHelper.calculate_lowest(table_data_local, 4))
                new_price = max(rounding_limit, min(
                    trend_price, lowest_in_country - rounding_limit))
            else:
                # No competition in our country, set price a bit higher.
                new_price = PyMkmHelper.round_up_to_limit(
                    rounding_limit, trend_price * 1.2)
        else:
            new_price = PyMkmHelper.round_up_to_limit(
                rounding_limit, trend_price)

        if new_price == None:
            raise ValueError('No price found!')
        else:
            return new_price

    def display_price_changes_table(self, changes_json):
        # table breaks because of progress bar rendering
        print('\nBest diffs:\n')
        sorted_best = sorted(
            changes_json, key=lambda x: x['price_diff'], reverse=True)[:10]
        self.draw_price_changes_table(
            i for i in sorted_best if i['price_diff'] > 0)
        print('\nWorst diffs:\n')
        sorted_worst = sorted(changes_json, key=lambda x: x['price_diff'])[:10]
        self.draw_price_changes_table(
            i for i in sorted_worst if i['price_diff'] < 0)

        print('\nTotal price difference: {}.'.format(
            str(round(sum(item['price_diff'] * item['count']
                          for item in sorted_best), 2))
        ))

    def draw_price_changes_table(self, sorted_best):
        print(tb.tabulate(
            [
                [item['count'],
                 item['name'],
                 u'\u2713' if item['foil'] else '',
                    item['old_price'],
                    item['price'],
                 item['price_diff']] for item in sorted_best],
            headers=['Count', 'Name', 'Foil?',
                     'Old price', 'New price', 'Diff'],
            tablefmt="simple"
        ))

    def get_stock_as_array(self, api):
        d = api.get_stock()

        keys = ['idArticle', 'idProduct', 'product', 'count',
                'price', 'isFoil', 'isSigned', 'language']  # TODO: [language][languageId]
        stock_list = [{x: y for x, y in article.items() if x in keys}
                      for article in d]
        return stock_list
