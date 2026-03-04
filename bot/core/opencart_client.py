"""Асинхронный клиент OpenCart REST API для создания заказов из бота."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlencode

import httpx
from loguru import logger

from .opencart_config import OpenCartConfig, get_opencart_config


class OpenCartAPIError(Exception):
    """Ошибка ответа OpenCart API (success отсутствует или в ответе есть error)."""

    def __init__(self, message: str, response: dict[str, Any] | None = None) -> None:
        self.response = response or {}
        super().__init__(message)


def _flatten_form(prefix: str, value: object) -> list[tuple[str, str]]:
    """Рекурсивно разворачивает словарь/список в плоский список пар для form-urlencoded."""
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for k, v in value.items():
            out.extend(_flatten_form(f"{prefix}[{k}]", v))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out.extend(_flatten_form(f"{prefix}[{i}]", v))
        return out
    return [(prefix, str(value))]


class OpenCartClient:
    """Клиент для вызовов OpenCart API (логин, клиент, адрес, корзина, заказ)."""

    def __init__(self, config: OpenCartConfig | None = None) -> None:
        """Инициализирует клиента OpenCart API.

        Args:
            config (Optional[OpenCartConfig]): Конфигурация подключения к OpenCart.
                Если не передана, используется конфигурация из окружения.
        """

        self._config = config or get_opencart_config()
        self._api_token: str | None = None
        # Используем синхронный httpx.Client и выполняем запросы в отдельном потоке,
        # чтобы не блокировать event loop aiogram.
        self._client: httpx.Client = httpx.Client(timeout=30.0)

    async def __aenter__(self) -> OpenCartClient:
        """Возвращает клиента для использования в контекстном менеджере."""

        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Закрывает HTTP-клиент при выходе из контекста."""

        await asyncio.to_thread(self._client.close)

    def _url(self, route: str) -> str:
        base = self._config.base_url.rstrip("/")
        return f"{base}/index.php?route={route}"

    def _url_with_token(self, route: str) -> str:
        url = self._url(route)
        if self._api_token:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_token={self._api_token}"
        return url

    async def _request(
        self,
        method: str,
        route: str,
        data: dict[str, Any] | list[tuple[str, str]] | None = None,
        use_token: bool = True,
    ) -> dict[str, Any]:
        """Выполняет HTTP-запрос к OpenCart API и обрабатывает ошибки.

        Args:
            method (str): HTTP-метод ("GET" или "POST").
            route (str): Значение параметра route в OpenCart.
            data (dict | list[tuple[str, str]] | None): Данные формы для POST-запроса.
            use_token (bool): Добавлять ли api_token к URL.

        Returns:
            dict[str, Any]: Распарсенный JSON-ответ.

        Raises:
            OpenCartAPIError: Ошибка HTTP или бизнес-ошибка OpenCart.
        """

        url = self._url_with_token(route) if use_token else self._url(route)
        form_data: list[tuple[str, str]] | None
        if data is None:
            form_data = None
        elif isinstance(data, list):
            form_data = data
        else:
            # Преобразуем в плоский form: key1=val1&key2=val2 (вложенные через [])
            form_data = []
            for k, v in data.items():
                form_data.extend(_flatten_form(k, v))

        if method == "GET":
            resp = await asyncio.to_thread(self._client.get, url)
        else:
            # Синхронный Client.post с data=list of tuples в части версий даёт TypeError
            # при сборке тела; кодируем форму вручную и передаём как content.
            body_bytes = urlencode(form_data or [], doseq=True).encode("utf-8")
            resp = await asyncio.to_thread(
                self._client.post,
                url,
                content=body_bytes,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        try:
            body = resp.json()
        except Exception as e:
            logger.error(
                "OpenCart API неверный JSON: url={url} status={status} body={body}",
                url=url,
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise OpenCartAPIError(f"Неверный ответ API: {e}") from e
        # OpenCart иногда возвращает list вместо dict (например api/shipping/address)
        if not isinstance(body, dict):
            body = {}
        if resp.status_code >= 400:
            raise OpenCartAPIError(
                f"HTTP {resp.status_code}: {body.get('error', body)}",
                response=body,
            )
        err = body.get("error")
        if err:
            if isinstance(err, dict):
                msg = err.get("warning") or err.get("key") or str(err)
            elif isinstance(err, list):
                msg = "; ".join(str(e) for e in err)
            else:
                msg = str(err)
            raise OpenCartAPIError(f"OpenCart error: {msg}", response=body)
        return body

    async def login(self) -> str:
        """Выполняет вход в API и сохраняет api_token.

        Returns:
            str: Токен сессии (api_token).

        Raises:
            OpenCartAPIError: Ошибка входа (неверный ключ, IP не в белом списке).
        """
        data = {
            "key": self._config.api_key,
            "username": self._config.api_username,
        }
        body = await self._request("POST", "api/login", data=data, use_token=False)
        token = body.get("api_token")
        if not token:
            raise OpenCartAPIError(
                "В ответе login нет api_token",
                response=body,
            )
        self._api_token = token
        logger.debug("OpenCart API: успешный логин, api_token получен")
        return token

    async def set_customer(
        self,
        *,
        customer_id: int = 0,
        firstname: str,
        lastname: str,
        email: str,
        telephone: str,
        customer_group_id: int | None = None,
        custom_field: dict[str, Any] | None = None,
    ) -> None:
        """Устанавливает данные клиента в сессии (гость или по customer_id).

        Args:
            customer_id: 0 для гостя.
            firstname: Имя.
            lastname: Фамилия.
            email: Email.
            telephone: Телефон.
            customer_group_id: ID группы (опционально).
            custom_field: Произвольные поля (опционально).

        Raises:
            OpenCartAPIError: Ошибка валидации или прав.
        """
        data: dict[str, Any] = {
            "customer_id": customer_id,
            "firstname": firstname,
            "lastname": lastname,
            "email": email,
            "telephone": telephone,
        }
        if customer_group_id is not None:
            data["customer_group_id"] = customer_group_id
        if custom_field is not None:
            data["custom_field"] = custom_field
        await self._request("POST", "api/customer", data=data)

    async def set_payment_address(
        self,
        *,
        firstname: str,
        lastname: str,
        address_1: str,
        city: str,
        zone_id: int,
        country_id: int,
        company: str = "",
        address_2: str = "",
        postcode: str = "",
        custom_field: dict[str, Any] | None = None,
    ) -> None:
        """Устанавливает адрес оплаты в сессии.

        Args:
            firstname: Имя.
            lastname: Фамилия.
            address_1: Адрес (улица, дом).
            city: Город.
            zone_id: ID региона (зоны) в OpenCart.
            country_id: ID страны в OpenCart.
            company: Компания (опционально).
            address_2: Адрес строка 2 (опционально).
            postcode: Индекс (опционально).
            custom_field: Доп. поля (опционально).

        Raises:
            OpenCartAPIError: Ошибка валидации или прав.
        """
        data: dict[str, Any] = {
            "firstname": firstname,
            "lastname": lastname,
            "company": company,
            "address_1": address_1,
            "address_2": address_2,
            "postcode": postcode,
            "city": city,
            "zone_id": zone_id,
            "country_id": country_id,
        }
        if custom_field is not None:
            data["custom_field"] = custom_field
        await self._request("POST", "api/payment/address", data=data)

    async def set_shipping_address(
        self,
        *,
        firstname: str,
        lastname: str,
        address_1: str,
        city: str,
        zone_id: int,
        country_id: int,
        company: str = "",
        address_2: str = "",
        postcode: str = "",
        custom_field: dict[str, Any] | None = None,
    ) -> None:
        """Устанавливает адрес доставки в сессии.

        Args:
            firstname: Имя.
            lastname: Фамилия.
            address_1: Адрес.
            city: Город.
            zone_id: ID зоны.
            country_id: ID страны.
            company: Компания.
            address_2: Адрес 2.
            postcode: Индекс.
            custom_field: Доп. поля.

        Raises:
            OpenCartAPIError: Ошибка валидации или прав.
        """
        data: dict[str, Any] = {
            "firstname": firstname,
            "lastname": lastname,
            "company": company,
            "address_1": address_1,
            "address_2": address_2,
            "postcode": postcode,
            "city": city,
            "zone_id": zone_id,
            "country_id": country_id,
        }
        if custom_field is not None:
            data["custom_field"] = custom_field
        await self._request("POST", "api/shipping/address", data=data)

    def _build_cart_form(self, products: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """Собирает form-data для api/cart/add (массив product)."""
        form: list[tuple[str, str]] = []
        for i, p in enumerate(products):
            form.append((f"product[{i}][product_id]", str(p["product_id"])))
            form.append((f"product[{i}][quantity]", str(p.get("quantity", 1))))
            opt = p.get("option")
            if opt and isinstance(opt, dict):
                for k, v in opt.items():
                    form.append((f"product[{i}][option][{k}]", str(v)))
            elif opt is None or (isinstance(opt, dict) and not opt):
                pass
            else:
                form.append((f"product[{i}][option]", json.dumps(opt)))
        return form

    async def cart_add(self, products: list[dict[str, Any]]) -> None:
        """Добавляет товары в корзину (предыдущая корзина очищается).

        Args:
            products: Список словарей с ключами product_id (int), quantity (int),
                option (dict product_option_id -> value, опционально).

        Raises:
            OpenCartAPIError: Ошибка (товар не найден, нет прав и т.д.).
        """
        form = self._build_cart_form(products)
        await self._request("POST", "api/cart/add", data=form)

    async def get_payment_methods(self) -> dict[str, Any]:
        """Возвращает доступные способы оплаты (после set_payment_address).

        Returns:
            dict: Ключи — коды методов (например cod.cod), значения — данные метода.

        Raises:
            OpenCartAPIError: Нет адреса оплаты или прав.
        """
        body = await self._request("GET", "api/payment/methods")
        return body.get("payment_methods", body)

    async def get_shipping_methods(self) -> dict[str, Any]:
        """Возвращает доступные способы доставки (после set_shipping_address и cart с доставкой).

        Returns:
            dict: Ключи — коды, значения — данные методов.

        Raises:
            OpenCartAPIError: Нет адреса доставки или прав.
        """
        body = await self._request("GET", "api/shipping/methods")
        return body.get("shipping_methods", body)

    async def add_order(
        self,
        *,
        payment_method: str,
        shipping_method: str | None = None,
        comment: str = "",
        order_status_id: int | None = None,
    ) -> int:
        """Создаёт заказ из текущей сессии (корзина, клиент, адреса должны быть заданы).

        Перед вызовом необходимо один раз получить способы оплаты (и доставки при необходимости),
        чтобы в сессии OpenCart были payment_methods: вызвать get_payment_methods()
        и при необходимости get_shipping_methods().

        Args:
            payment_method: Код способа оплаты (из get_payment_methods), например «cod.cod».
            shipping_method: Код способа доставки (если нужна доставка), например «flat.flat».
            comment: Комментарий к заказу.
            order_status_id: ID статуса в OpenCart (по умолчанию из конфига).

        Returns:
            int: order_id созданного заказа в OpenCart.

        Raises:
            OpenCartAPIError: Ошибка создания заказа.
        """
        data: dict[str, Any] = {
            "payment_method": payment_method,
            "comment": comment,
            "order_status_id": (order_status_id if order_status_id is not None else self._config.order_status_id),
        }
        if shipping_method is not None:
            data["shipping_method"] = shipping_method
        body = await self._request("POST", "api/order/add", data=data)
        oid = body.get("order_id")
        if oid is None:
            raise OpenCartAPIError("В ответе order/add нет order_id", response=body)
        return int(oid)

    async def add_order_history(
        self,
        order_id: int,
        order_status_id: int,
        comment: str = "",
        notify: bool = False,
    ) -> None:
        """Добавляет запись в историю заказа OpenCart (например, подтверждение оплаты).

        Вызов может не поддерживаться стандартным API OpenCart; при ошибке — логируем и не
        пробрасываем исключение, чтобы не ломать поток бота.

        Args:
            order_id: ID заказа в OpenCart.
            order_status_id: ID статуса (например «В обработке»).
            comment: Комментарий к записи (например «Платеж номер … подтвержден»).
            notify: Уведомлять ли клиента.
        """
        data: dict[str, Any] = {
            "order_id": order_id,
            "order_status_id": order_status_id,
            "comment": comment,
            "notify": 1 if notify else 0,
        }
        await self._request("POST", "api/order/history", data=data)

    async def ensure_logged_in(self) -> None:
        """Выполняет логин, если токен ещё не получен."""
        if not self._api_token:
            await self.login()
