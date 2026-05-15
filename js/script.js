// script.js

// Function to fetch crypto data from CoinGecko API
async function fetchCryptoData() {
    const url = 'https://api.coingecko.com/api/v3/coins/markets';
    const params = new URLSearchParams({
        vs_currency: 'usd',   // Currency in which you want the prices
        ids: 'bitcoin,ethereum,chainlink,bittensor,hyperliquid,ondo-finance'
    });

    try {
        const response = await fetch(`${url}?${params}`);
        const data = await response.json();

        // Display the data on the webpage
        const cryptoList = document.getElementById('crypto-list');
        cryptoList.innerHTML = ''; // Clear the list before appending new data

        // Loop through the data and create an HTML element for each cryptocurrency
        data.forEach(coin => {
            const coinElement = document.createElement('div');
            coinElement.classList.add('coin-item');
            coinElement.innerHTML = `
                <h3>${coin.name} (${coin.symbol.toUpperCase()})</h3>
                <p>Price: $${coin.current_price}</p>
                <p>Market Cap: $${coin.market_cap.toLocaleString()}</p>
                <p>24h Change: ${coin.price_change_percentage_24h.toFixed(2)}%</p>
            `;
            cryptoList.appendChild(coinElement);
        });
    } catch (error) {
        console.error('Error fetching crypto data:', error);
        document.getElementById('crypto-list').innerHTML = `<p>Failed to load data. Please try again later.</p>`;
    }
}

// Call the function to load crypto data
fetchCryptoData();
