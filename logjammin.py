#!/usr/bin/env python3

import re
import json
import argparse
import math
from os.path import expanduser
from datetime import datetime
from pytz import timezone
from jira import JIRA
from collections import OrderedDict


class LogJammin:
    mode = 'date'
    current_date = None
    parse_only = False
    logs = []
    tickets = []
    jira = None
    time_zone = None
    now = None

    def __init__(self, filename, parse_only):
        self.parse_only = parse_only

        try:
            config = self.load_config()
            self.time_zone = timezone(config['time_zone'])
            self.now = self.time_zone.localize(datetime.now())
        except Exception as e:
            self.exit_with_error(e)

        if not self.parse_only:
            print('Connecting to JIRA...', end='', flush=True)
            try:
                self.jira = JIRA(
                    server=config['host'],
                    basic_auth=(config['user'], config['password'])
                )
            except Exception as e:
                self.exit_with_error(e)
            print('\033[92mdone\033[0m')

        print('Loading logs...', end='', flush=True)
        try:
            self.load_logs(filename)
        except Exception as e:
            self.exit_with_error(e)
        print('\033[92mdone\033[0m')

        if not len(self.logs):
            self.exit_with_error('No logs found')

        self.print_summary()

        if not self.parse_only:
            while True:
                run = input('Upload logs to JIRA? (y/n): ').lower().strip()
                if run == 'n':
                    self.exit_with_success()
                elif run == 'y':
                    break
            try:
                for (i, log) in enumerate(self.logs):
                    print('Saving log {}/{}: ({})...'.format(i + 1, len(self.logs), self.format_log(log)), end='', flush=True)
                    self.upload_log(log)
                    print('\033[92mdone\033[0m')
            except Exception as e:
                self.exit_with_error(e)

        self.exit_with_success()

    def print_summary(self):
        logs_by_date = OrderedDict()
        total_minutes = 0
        print('\033[94m{}\033[0m'.format(80 * '='))
        print('\033[93mSummary:\033[0m')
        for log in self.logs:
            date = log['date'].strftime('%Y-%m-%d')
            if date not in logs_by_date:
                logs_by_date[date] = {
                    'logs': [],
                    'total_time_minutes': 0
                }
            logs_by_date[date]['logs'].append(log)
            logs_by_date[date]['total_time_minutes'] += 60 * log['time']['hours']
            logs_by_date[date]['total_time_minutes'] += log['time']['minutes']

        for date, summary in logs_by_date.items():
            print('\n\033[93m{}\033[0m'.format(date))
            hours = math.floor(summary['total_time_minutes'] / 60)
            minutes = math.floor(summary['total_time_minutes'] % 60)
            total_minutes += summary['total_time_minutes']
            for log in summary['logs']:
                print('  {}: {}'.format(log['ticket'], self.format_time(log['time']['hours'], log['time']['minutes'])))
            print('\033[93mTotal: {} logs, {}\033[0m'.format(len(summary['logs']), self.format_time(hours, minutes)))

        summary_hours = math.floor(total_minutes / 60)
        summary_minutes = math.floor(total_minutes % 60)
        print(
            '\n\033[93mSum Total: {} days, {} logs, {}\033[0m'.format(
                len(logs_by_date),
                len(self.logs),
                self.format_time(summary_hours, summary_minutes)
            )
        )
        print('\033[94m{}\033[0m'.format(80 * '='))

    def exit_with_success(self):
        print('\033[92mDone\033[0m')
        exit()

    def exit_with_error(self, e):
        print('\n\033[91m{}\033[0m'.format(str(e)))
        exit(1)

    def format_log(self, log):
        return 'date={}, ticket={}, time={}'.format(log['date'].strftime('%Y-%m-%d'), log['ticket'], self.format_time(log['time']['hours'], log['time']['minutes']))

    def format_time(self, hours, minutes):
        time_str = ''
        if hours:
            time_str += '{}h '.format(hours)
        if minutes:
            time_str += '{}m'.format(minutes)
        return time_str.strip()

    def load_config(self):
        try:
            required_keys = ['user', 'password', 'host', 'time_zone']
            with open(expanduser('~/.logjammin')) as f:
                config = json.load(f)
            for key in required_keys:
                if key not in config:
                    raise Exception('missing key \'{}\''.format(key))
            return config
        except Exception as e:
            raise Exception('Error parsing ~/.logjammin: {}'.format(e)) from None

    def load_logs(self, filename):
        line_no = 0
        loading_pct = 0
        lines = []
        with open(filename, 'r') as fp:
            lines = fp.read().splitlines()
        for line in lines:
            line_no += 1
            if not len(line.strip()):
                continue
            try:
                self.parse_line(line)
            except Exception as e:
                raise Exception('Error on line {}: {}'.format(line_no, str(e))) from None
            prev_loading_pct = loading_pct
            loading_pct = math.floor(line_no / len(lines) * 100)
            print(
                '{}{}%'.format(
                    '\b' * (len(str(prev_loading_pct)) + 1 if prev_loading_pct else 0),
                    loading_pct
                ),
                end='',
                flush=True
            )
        if len(lines):
            print('\b' * 4, end='', flush=True) # 100%
        self.logs.sort(key=lambda k: (k['date'], k['ticket'].split('-')[0], int(k['ticket'].split('-')[1])))

    def upload_log(self, log):
        time_spent = '{}h {}m'.format(log['time']['hours'], log['time']['minutes'])

        self.jira.add_worklog(
            issue=log['ticket'],
            timeSpent=time_spent,
            started=log['date']
        )

    def parse_line(self, line):
        normalized_line = line.replace(' ', '').upper()

        if self.mode == 'date':
            try:
                self.current_date = self.parse_date_line(normalized_line)
                self.mode = 'time_log'
            except Exception as e:
                raise Exception('String \'{}\' is invalid: {}'.format(line, str(e))) from None
        elif self.mode == 'time_log':
            try:
                ticket, time = self.parse_time_log_line(normalized_line)
                self.add_log(ticket, time)
                self.mode = 'date_or_time_log'
            except Exception as e:
                raise Exception('String \'{}\' is invalid: {}'.format(line, e)) from None
        elif self.mode == 'date_or_time_log':
            try:
                self.mode = 'date'
                return self.parse_line(line)
            except Exception as e:
                try:
                    self.mode = 'time_log'
                    return self.parse_line(line)
                except Exception as e:
                    raise Exception('String \'{}\' is invalid: {}'.format(line, str(e))) from None
        else:
            raise Exception('Invalid mode \'{}\''.format(self.mode))

    def parse_date_line(self, line):
        date_match = re.match(r'^(?P<year>\d{4})-?(?P<month>\d{2})-?(?P<day>\d{2})$', line)
        if not date_match:
            raise Exception('Pattern not matched')

        date = self.time_zone.localize(
            datetime(int(date_match.group('year')), int(date_match.group('month')), int(date_match.group('day')))
        )
        if date > self.now:
            raise Exception('Date is in the future')
        return date

    def parse_time_log_line(self, line):
        ticket_match_re = r'(?P<ticket>[A-Z][A-Z0-9]+-\d+)'
        dec_hours_re = r'(?P<dec_hours>\d*(\.\d+)?)H?'
        hours_mins_re = r'((?P<hours>\d+)H)?((?P<minutes>\d+)M)?'

        log = re.match(r'^' + ticket_match_re + r',(' + hours_mins_re + r'|' + dec_hours_re + ')$', line)

        if not log:
            raise Exception('Pattern not matched')

        if log.group('dec_hours'):
            dec_hours = float(log.group('dec_hours'))
            hours = math.floor(dec_hours)
            minutes = math.floor(60 * (dec_hours % 1))
        else:
            hours = int(log.group('hours') or 0)
            minutes = int(log.group('minutes') or 0)

        ticket = log.group('ticket').upper()
        if not self.parse_only:
            self.assert_ticket_exists(ticket)
        time = (hours, minutes)

        return (ticket, time)

    def assert_ticket_exists(self, ticket):
        if ticket in self.tickets:
            return
        try:
            self.jira.issue(ticket, fields='key')
            self.tickets.append(ticket)
        except Exception as e:
            raise Exception('Failed to get ticket info for {}'.format(ticket)) from None

    def add_log(self, ticket, time):
        self.logs.append({
            'date': self.current_date,
            'ticket': ticket,
            'time': {
                'hours': time[0],
                'minutes': time[1]
            }
        })

parser = argparse.ArgumentParser()
parser.add_argument('file', type=str, help='the file to load')
parser.add_argument('-p', '--parse-only', action='store_true', help='parse the file only (don\'t verify tickets or upload logs)')
args = parser.parse_args()

LogJammin(filename=args.file, parse_only=args.parse_only)
