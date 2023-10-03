'''
Created on 2023年1月25日

@author: wfeng007
'''
import requests
import pandas

def getCandlesDf(pairName:str='ETH-USDT',period:str="1m",limit:int=10):
    '''
    ohlcv 格式蜡烛线信息，返回dataframe结构
    '''
    url='https://www.okx.com/api/v5/market/candles'
    params = {"instId": f'{pairName}',"bar": f'{period}',"limit": f"{limit}"}
    
    records = requests.get(url=url, params=params).json()
    # return [Decimal(str(record[4])) for record in records]
    
    if records['code'] == '0':
        cdtLs=records['data']
        '''
        ts    String    开始时间，Unix时间戳的毫秒数格式，如 1597026383085
        o    String    开盘价格
        h    String    最高价格
        l    String    最低价格
        c    String    收盘价格
        vol    String    交易量，以张为单位
        如果是衍生品合约，数值为合约的张数。
        如果是币币/币币杠杆，数值为交易货币的数量。
        volCcy    String    交易量，以币为单位
        如果是衍生品合约，数值为交易货币的数量。
        如果是币币/币币杠杆，数值为计价货币的数量。
        volCcyQuote    String    交易量，以计价货币为单位
        如：BTC-USDT 和 BTC-USDT-SWAP, 单位均是 USDT；
        BTC-USD-SWAP 单位是 USD
        confirm    String    K线状态
        0 代表 K 线未完结，1 代表 K 线已完结。
        '''
        columns = ["timestamp", "open", "highest", "lowest", "close","volume","volumeCcy","volumeCcyQuote","confirm"]
        
        # data=cdtLs
        # 格式转换
        data = []
        for candleData in cdtLs:
            tsTxt = "n/a" if int(candleData[0]) <= 0 else pandas.Timestamp(int(candleData[0]), unit='ms').strftime('%Y-%m-%d %H:%M:%S.%f')
            data.append([
                tsTxt,
                candleData[1],#
                candleData[2],
                candleData[3],
                float(candleData[4]),
                candleData[5],
                candleData[6],
                candleData[7],
                candleData[8],
            ])
        if not data :
            raise ValueError("no data!") #空数组或None等无正常数据，fixme其实空数组可以反馈内容。
        reDf = pandas.DataFrame(data=data, columns=columns)
        return reDf
    else:
        raise IOError(records['msg'])

    # def getCandles(self):
    #     url='https://www.okx.com/api/v5/market/candles'
    #     params = {"instId": f'{self.BASE_ASSET}-{self.QUOTE_ASSET}',"bar": "1m","limit": "10"}
    #
    #     records = requests.get(url=url, params=params).json()
    #
    #     if records["code"] == '0' :
    #         re=[Decimal(str(record['data'])) for record in records]
    #     #[Decimal(str(record[4])) for record in records]
    #     # return [Decimal(str(record[4])) for record in records]
    #     return records


if __name__ == '__main__':
    df=getCandlesDf()
    lines = []
    lines.extend(["getCandles()返回:"]+[" " + line for line in df.to_string(index=False).split("\n")])
    print("\n".join(lines))
    pass



