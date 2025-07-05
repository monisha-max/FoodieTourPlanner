### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/monisha-max/FoodieTourPlanner.git
   cd FoodieTourPlanner
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API keys**
   
   Create a `.streamlit/secrets.toml` file:
   ```toml
   [julep]
   api_key = "your_julep_api_key"

   [weather]
   api_key = "your_weather_api_key"

   [unsplash]
   api_key = "your_unsplash_api_key"
   ```

4. **Run the application**
   ```bash
   streamlit run app.py
   ```

