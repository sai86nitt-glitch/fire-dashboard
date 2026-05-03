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
    dbc.Container([
        dbc.NavbarBrand(
            [
                html.Img(src="/assets/logo.svg", height="32px",
                         style={"marginRight": "8px", "verticalAlign": "middle"}),
                html.Span("Swai", style={
                    "fontWeight": "700", "fontSize": "18px",
                    "color": "#e0e0e0", "letterSpacing": "0.04em",
                    "verticalAlign": "middle",
                }),
                html.Span(" Finance", style={
                    "fontWeight": "400", "fontSize": "14px",
                    "color": "#888", "letterSpacing": "0.02em",
                    "verticalAlign": "middle",
                }),
            ],
            href="/",
            style={"display": "flex", "alignItems": "center"},
        ),
        # Desktop nav — hidden on mobile
        dbc.Nav([
            dbc.NavLink("🏠 Dashboard",  href="/",          active="exact"),
            dbc.NavLink("💸 Expenses",   href="/expenses",  active="exact"),
            dbc.NavLink("📊 Portfolio",  href="/portfolio", active="exact"),
        ], navbar=True, pills=True, className="d-none d-md-flex"),
    ], fluid=True),
    color="#1e1e2e",
    dark=True,
    fixed="top",
    style={"borderBottom": "1px solid #2a2a3e", "zIndex": 1050},
)

# Mobile bottom tab bar — visible only on small screens
bottom_tabs = html.Div(
    [
        dcc.Link(
            [html.Span("🏠", className="tab-icon"), html.Span("Dashboard", className="tab-label")],
            href="/", className="bottom-tab", id="tab-home",
        ),
        dcc.Link(
            [html.Span("💸", className="tab-icon"), html.Span("Expenses", className="tab-label")],
            href="/expenses", className="bottom-tab", id="tab-expenses",
        ),
        dcc.Link(
            [html.Span("📊", className="tab-icon"), html.Span("Portfolio", className="tab-label")],
            href="/portfolio", className="bottom-tab", id="tab-portfolio",
        ),
    ],
    id="bottom-tab-bar",
    className="d-flex d-md-none",
)

app.layout = html.Div([
    dcc.Location(id="url"),
    navbar,
    html.Div(
        dash.page_container,
        style={"padding": "72px 20px 40px"},
        className="page-content",
    ),
    bottom_tabs,
], style={"background": "#0e0e1a", "minHeight": "100vh"})

@callback(
    Output("tab-home",      "className"),
    Output("tab-expenses",  "className"),
    Output("tab-portfolio", "className"),
    Input("url", "pathname"),
)
def _highlight_tab(path):
    base = "bottom-tab"
    active = f"{base} active"
    home      = active if path == "/"          else base
    expenses  = active if path == "/expenses"  else base
    portfolio = active if path == "/portfolio" else base
    return home, expenses, portfolio


if __name__ == "__main__":
    app.run(debug=True, port=8502)
