# django-log-panel

[![Latest on Django Packages](https://img.shields.io/badge/Django_Packages-django--log--panel-8c3c26.svg)](https://djangopackages.org/packages/p/django-log-panel/)
[![Docs](https://img.shields.io/badge/docs-rreiter3.github.io-blue)](https://rreiter3.github.io/django-log-panel/)

`django-log-panel` displays your Django logs inside Django admin as a per-logger status dashboard with searchable log entries and optional threshold alerts, without a separate service to run.

<p align="center">
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main.png"
      alt="Log panel dashboard showing per-logger health cards"
      width="100%"
    />
  </a>
</p>

<p align="center">
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main_2.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/main_2.png"
      alt="Log panel dashboard showing a 90 day logger timeline"
      width="100%"
    />
  </a>
</p>

<p align="center">
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter.png"
      alt="Log detail view with message search and paginated entries"
      width="49%"
    />
  </a>
  <a href="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter_2.png">
    <img
      src="https://raw.githubusercontent.com/rreiter3/django-log-panel/main/docs/images/filter_2.png"
      alt="Log detail view with the level filter dropdown open"
      width="49%"
    />
  </a>
</p>

## Features

- A status-page style dashboard in Django admin, with one health card per logger.
- A searchable, filterable log table for drilling into individual entries.
- MongoDB and SQL storage backends, depending on how you want to store logs.
- Threshold alerts through a Django signal that your application can react to.
- Configurable ranges, colors, page size, title, and access control.
- Automatic root-handler setup by default, with manual `LOGGING` control when needed.
