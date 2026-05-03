import dash
import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, callback

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="Swai Finance Dash",
    update_title=None,
    index_string="""<!DOCTYPE html>
<html>
  <head>
    {%metas%}
    <title>{%title%}</title>
    <link rel="icon" type="image/svg+xml" href="/assets/logo.svg">
    {%css%}
  </head>
  <body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
  </body>
</html>""",
)
server = app.server

navbar = dbc.Navbar(
    dbc.Container(
        [
            # Left: browser-style tabs
            html.Div(
                [
                    dcc.Link(
                        [html.Span("🏠", className="nav-tab-icon"),
                         html.Span("Dashboard", className="nav-tab-label")],
                        href="/", className="nav-tab", id="tab-home",
                    ),
                    dcc.Link(
                        [html.Span("💸", className="nav-tab-icon"),
                         html.Span("Expenses", className="nav-tab-label")],
                        href="/expenses", className="nav-tab", id="tab-expenses",
                    ),
                    dcc.Link(
                        [html.Span("📊", className="nav-tab-icon"),
                         html.Span("Portfolio", className="nav-tab-label")],
                        href="/portfolio", className="nav-tab", id="tab-portfolio",
                    ),
                ],
                className="nav-tab-group",
            ),
            # Right: brand
            dbc.NavbarBrand(
                [
                    html.Img(src="/assets/logo.svg", height="26px",
                             style={"marginRight": "7px", "verticalAlign": "middle"}),
                    html.Span("Swai", style={
                        "fontWeight": "700", "fontSize": "16px",
                        "color": "#e0e0e0", "letterSpacing": "0.04em",
                        "verticalAlign": "middle",
                    }),
                    html.Span(" Finance", style={
                        "fontWeight": "400", "fontSize": "13px",
                        "color": "#888", "verticalAlign": "middle",
                    }),
                ],
                href="/",
                className="ms-auto",
                style={"display": "flex", "alignItems": "center"},
            ),
        ],
        fluid=True,
        style={"alignItems": "flex-end", "height": "100%"},
    ),
    color="#13131f",
    dark=True,
    fixed="top",
    style={"height": "52px", "borderBottom": "1px solid #2a2a3e", "zIndex": 1050},
)

app.layout = html.Div([
    dcc.Location(id="url"),
    navbar,
    html.Div(
        dash.page_container,
        className="page-content",
    ),
], style={"background": "#0e0e1a", "minHeight": "100vh"})

@callback(
    Output("tab-home",      "className"),
    Output("tab-expenses",  "className"),
    Output("tab-portfolio", "className"),
    Input("url", "pathname"),
)
def _highlight_tab(path):
    base = "nav-tab"
    active = f"{base} active"
    home      = active if path == "/"          else base
    expenses  = active if path == "/expenses"  else base
    portfolio = active if path == "/portfolio" else base
    return home, expenses, portfolio


if __name__ == "__main__":
    app.run(debug=True, port=8502)
