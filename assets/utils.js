export function call(method, url, data) {
  let params = {
    method: method,
    credentials: 'same-origin'
  };
  if (method == 'post') {
    (params.headers = { 'Content-Type': 'application/json' }),
      (params.body = data && JSON.stringify(data));
  }
  return fetch(url, params).then(response => {
    let res;
    if (response.headers.get('Content-Length') == '0') {
      res = response.text();
    } else {
      res = response.json();
    }
    if (!response.ok) {
      return res || { errors: response };
    } else {
      return res;
    }
  });
}

export function trancate(value, max = 15, simbol = '…') {
  max = max || 15;
  if (value.length > max) {
    value = value.slice(0, max - 1) + simbol;
  }
  return value;
}
